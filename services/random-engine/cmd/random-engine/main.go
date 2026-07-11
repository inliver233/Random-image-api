package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sync"
	"time"
)

// Skeleton HTTP surface for Random Engine.
// Full in-memory index lands in Phase 3; this binary freezes the wire contract.

type pickRequest struct {
	Filters  map[string]any `json:"filters"`
	Strategy string         `json:"strategy"`
	Quality  map[string]any `json:"quality"`
	Seed     *string        `json:"seed"`
	Limit    int            `json:"limit"`
	Debug    bool           `json:"debug"`
}

type pickItem struct {
	ID        int64  `json:"id"`
	IllustID  int64  `json:"illust_id"`
	PageIndex int    `json:"page_index"`
	Ext       string `json:"ext"`
	EdgePath  string `json:"edge_path"`
}

type pickResponse struct {
	OK    bool       `json:"ok"`
	Code  string     `json:"code"`
	Items []pickItem `json:"items"`
	Debug any        `json:"debug,omitempty"`
}

type healthResponse struct {
	OK               bool   `json:"ok"`
	Service          string `json:"service"`
	IndexSize        int    `json:"index_size"`
	SnapshotRevision string `json:"snapshot_revision"`
}

type engineState struct {
	mu       sync.RWMutex
	size     int
	revision string
}

func main() {
	addr := envOr("RANDOM_ENGINE_ADDR", ":8091")
	st := &engineState{revision: "empty"}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		st.mu.RLock()
		defer st.mu.RUnlock()
		writeJSON(w, http.StatusOK, healthResponse{
			OK:               true,
			Service:          "random-engine",
			IndexSize:        st.size,
			SnapshotRevision: st.revision,
		})
	})
	mux.HandleFunc("/v1/pick", func(w http.ResponseWriter, r *http.Request) {
		handlePick(w, r, st)
	})
	mux.HandleFunc("/v1/feed", func(w http.ResponseWriter, r *http.Request) {
		handlePick(w, r, st)
	})
	mux.HandleFunc("/v1/admin/snapshot", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var body struct {
			Revision string `json:"revision"`
			Images   []any  `json:"images"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		st.mu.Lock()
		st.size = len(body.Images)
		if body.Revision != "" {
			st.revision = body.Revision
		} else {
			st.revision = time.Now().UTC().Format(time.RFC3339Nano)
		}
		st.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "index_size": st.size, "revision": st.revision})
	})
	mux.HandleFunc("/v1/admin/events", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		// Acknowledge only; real apply in Phase 3.
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "applied": 0})
	})

	log.Printf("random-engine skeleton listening on %s", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Fatal(err)
	}
}

func handlePick(w http.ResponseWriter, r *http.Request, st *engineState) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var req pickRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "bad json", http.StatusBadRequest)
		return
	}
	if req.Limit < 1 {
		req.Limit = 1
	}
	if req.Limit > 100 {
		req.Limit = 100
	}
	if req.Strategy == "" {
		req.Strategy = "random"
	}
	if req.Strategy != "random" && req.Strategy != "quality" {
		http.Error(w, "unsupported strategy", http.StatusBadRequest)
		return
	}

	st.mu.RLock()
	size := st.size
	rev := st.revision
	st.mu.RUnlock()

	// Empty index → stable NO_MATCH (BFF may fall back to SQL).
	resp := pickResponse{OK: true, Code: "NO_MATCH", Items: []pickItem{}}
	if size == 0 {
		if req.Debug {
			resp.Debug = map[string]any{
				"reason":   "empty_index",
				"revision": rev,
			}
		}
		writeJSON(w, http.StatusOK, resp)
		return
	}

	// Placeholder until real picker lands.
	if req.Debug {
		resp.Debug = map[string]any{
			"reason":   "picker_not_implemented",
			"revision": rev,
			"size":     size,
		}
	}
	writeJSON(w, http.StatusOK, resp)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
