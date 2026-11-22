package metrics

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// Metrics holds all gateway Prometheus instruments behind one registry.
type Metrics struct {
	Registry *prometheus.Registry

	HTTPRequests  *prometheus.CounterVec
	HTTPDuration  *prometheus.HistogramVec
	AssetsCreated *prometheus.CounterVec
	UploadBytes   prometheus.Counter
	Judgments     *prometheus.CounterVec
	AssetFailures prometheus.Counter
}

func New() *Metrics {
	registry := prometheus.NewRegistry()
	registry.MustRegister(prometheus.NewGoCollector())
	registry.MustRegister(prometheus.NewProcessCollector(prometheus.ProcessCollectorOpts{}))

	m := &Metrics{
		Registry: registry,
		HTTPRequests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "adguard_http_requests_total",
			Help: "HTTP requests by method, route and status code.",
		}, []string{"method", "route", "status"}),
		HTTPDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "adguard_http_request_duration_seconds",
			Help:    "HTTP request latency by method and route.",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
		}, []string{"method", "route"}),
		AssetsCreated: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "adguard_assets_created_total",
			Help: "Assets accepted for upload by asset type.",
		}, []string{"type"}),
		UploadBytes: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "adguard_upload_bytes_total",
			Help: "Total bytes accepted via chunk upload.",
		}),
		Judgments: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "adguard_judgments_total",
			Help: "Judgments produced by decision.",
		}, []string{"decision"}),
		AssetFailures: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "adguard_asset_failures_total",
			Help: "Assets that failed the judging pipeline permanently.",
		}),
	}
	registry.MustRegister(m.HTTPRequests, m.HTTPDuration, m.AssetsCreated, m.UploadBytes,
		m.Judgments, m.AssetFailures)
	return m
}

// Handler exposes /metrics.
func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.Registry, promhttp.HandlerOpts{})
}
