package httpapi

import (
	"encoding/json"
	"log/slog"
	"net/http"
)

type errorPayload struct {
	Error     errorDetail `json:"error"`
	RequestID string      `json:"request_id,omitempty"`
}

type errorDetail struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Details any    `json:"details,omitempty"`
}

// writeError emits the same error envelope as the Java control plane:
// {"error":{"code","message","details"?},"request_id":"..."}
func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string, details any) {
	writeJSON(w, status, errorPayload{
		Error:     errorDetail{Code: code, Message: message, Details: details},
		RequestID: requestIDFrom(r),
	})
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if payload == nil {
		return
	}
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		slog.Error("write response failed", "err", err)
	}
}
