package queue

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/redis/go-redis/v9"
)

// Producer enqueues audit tasks.
type Producer interface {
	Enqueue(ctx context.Context, task model.AuditTask) error
}

// Consumer runs a blocking consume loop until the context is cancelled.
type Consumer interface {
	Run(ctx context.Context, handler func(context.Context, model.AuditTask) error) error
}

// RedisStreams is the M1 queue: Redis Streams with a consumer group.
//
// Design notes:
//   - at-least-once delivery (handler must be idempotent -> judgment insert keyed by asset);
//   - retries are explicit re-publishes with attempt+1 (keeps the pending list short);
//   - after maxAttempts the task goes to the DLQ stream and is acked, so poison messages
//     can never block the pipeline.
type RedisStreams struct {
	client      *redis.Client
	stream      string
	group       string
	consumer    string
	maxAttempts int
	logger      *slog.Logger
}

func NewRedisStreams(client *redis.Client, stream, group, consumer string, maxAttempts int,
	logger *slog.Logger) *RedisStreams {
	return &RedisStreams{
		client: client, stream: stream, group: group, consumer: consumer,
		maxAttempts: maxAttempts, logger: logger,
	}
}

func (q *RedisStreams) Enqueue(ctx context.Context, task model.AuditTask) error {
	return q.client.XAdd(ctx, &redis.XAddArgs{
		Stream: q.stream,
		Values: map[string]any{
			"asset_id":  task.AssetID,
			"tenant_id": task.TenantID,
			"attempt":   task.Attempt,
		},
	}).Err()
}

func (q *RedisStreams) Run(ctx context.Context, handler func(context.Context, model.AuditTask) error) error {
	if err := q.ensureGroup(ctx); err != nil {
		return err
	}
	for ctx.Err() == nil {
		streams, err := q.client.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    q.group,
			Consumer: q.consumer,
			Streams:  []string{q.stream, ">"},
			Count:    10,
			Block:    5 * time.Second,
		}).Result()
		if errors.Is(err, redis.Nil) {
			continue
		}
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			q.logger.Error("queue read failed", "err", err)
			time.Sleep(time.Second)
			continue
		}
		for _, stream := range streams {
			for _, msg := range stream.Messages {
				q.handle(ctx, msg, handler)
			}
		}
	}
	return nil
}

func (q *RedisStreams) handle(ctx context.Context, msg redis.XMessage, handler func(context.Context, model.AuditTask) error) {
	task, err := parseTask(msg)
	if err != nil {
		q.logger.Error("poison message moved to dlq", "id", msg.ID, "err", err)
		q.toDLQ(ctx, msg, err.Error())
		q.ack(ctx, msg.ID)
		return
	}
	if err := handler(ctx, task); err != nil {
		if task.Attempt+1 >= q.maxAttempts {
			q.logger.Error("audit task exhausted retries -> dlq",
				"asset_id", task.AssetID, "attempt", task.Attempt, "err", err)
			q.toDLQ(ctx, msg, err.Error())
			q.ack(ctx, msg.ID)
			return
		}
		q.logger.Warn("audit task failed, retrying",
			"asset_id", task.AssetID, "attempt", task.Attempt, "err", err)
		retry := model.AuditTask{AssetID: task.AssetID, TenantID: task.TenantID, Attempt: task.Attempt + 1}
		if err := q.Enqueue(ctx, retry); err != nil {
			q.logger.Error("failed to re-publish retry", "err", err)
			return // do not ack: message stays pending for redelivery
		}
	}
	q.ack(ctx, msg.ID)
}

func (q *RedisStreams) ensureGroup(ctx context.Context) error {
	err := q.client.XGroupCreateMkStream(ctx, q.stream, q.group, "0").Err()
	if err != nil && !contains(err.Error(), "BUSYGROUP") {
		return fmt.Errorf("create consumer group: %w", err)
	}
	return nil
}

func (q *RedisStreams) ack(ctx context.Context, id string) {
	if err := q.client.XAck(ctx, q.stream, q.group, id).Err(); err != nil {
		q.logger.Warn("queue ack failed", "id", id, "err", err)
	}
}

func (q *RedisStreams) toDLQ(ctx context.Context, msg redis.XMessage, reason string) {
	values := make(map[string]any, len(msg.Values)+2)
	for k, v := range msg.Values {
		values[k] = v
	}
	values["failed_reason"] = reason
	values["failed_at"] = time.Now().UTC().Format(time.RFC3339)
	if err := q.client.XAdd(ctx, &redis.XAddArgs{Stream: q.stream + ":dlq", Values: values}).Err(); err != nil {
		q.logger.Error("failed to write dlq", "err", err)
	}
}

func parseTask(msg redis.XMessage) (model.AuditTask, error) {
	assetID, ok := msg.Values["asset_id"].(string)
	if !ok || assetID == "" {
		return model.AuditTask{}, fmt.Errorf("missing asset_id")
	}
	tenantID, ok := msg.Values["tenant_id"].(string)
	if !ok || tenantID == "" {
		return model.AuditTask{}, fmt.Errorf("missing tenant_id")
	}
	task := model.AuditTask{AssetID: assetID, TenantID: tenantID}
	if raw, ok := msg.Values["attempt"].(string); ok {
		if n, err := strconv.Atoi(raw); err == nil {
			task.Attempt = n
		}
	}
	return task, nil
}

func contains(haystack, needle string) bool {
	return len(haystack) >= len(needle) && (func() bool {
		for i := 0; i+len(needle) <= len(haystack); i++ {
			if haystack[i:i+len(needle)] == needle {
				return true
			}
		}
		return false
	})()
}
