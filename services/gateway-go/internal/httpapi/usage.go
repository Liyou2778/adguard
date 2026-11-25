package httpapi

import (
	"net/http"
	"time"
)

// usage returns the authenticated tenant's judged-asset usage and cost for a date range
// (defaults to the last 30 days). Daily billing exports live in the control plane.
func (s *Server) usage(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	to := time.Now().UTC()
	from := to.AddDate(0, 0, -29)
	if value := r.URL.Query().Get("from"); value != "" {
		parsed, err := time.Parse("2006-01-02", value)
		if err != nil {
			writeError(w, r, http.StatusBadRequest, "INVALID_DATE",
				"from must be YYYY-MM-DD", nil)
			return
		}
		from = parsed
	}
	if value := r.URL.Query().Get("to"); value != "" {
		parsed, err := time.Parse("2006-01-02", value)
		if err != nil {
			writeError(w, r, http.StatusBadRequest, "INVALID_DATE",
				"to must be YYYY-MM-DD", nil)
			return
		}
		to = parsed
	}
	if from.After(to) {
		writeError(w, r, http.StatusBadRequest, "INVALID_RANGE", "from must be <= to", nil)
		return
	}
	summary, err := s.store.UsageSummary(r.Context(), tenant, from, to)
	if err != nil {
		s.internalError(w, r, "load usage summary", err)
		return
	}
	writeJSON(w, http.StatusOK, summary)
}
