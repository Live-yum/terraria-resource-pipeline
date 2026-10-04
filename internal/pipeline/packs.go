package pipeline

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"

	"terraria-resource-pipeline/internal/artifact"
)

// Each public pack stays small enough for lazy client loading. Large game
// draw-plan NDJSON is read one line at a time, never materialized as one array.
func packFamily(store *artifact.Store, filename string, expected int, drawPlans bool) ([]Pack, []Pack, error) {
	file, err := os.Open(filename)
	if err != nil {
		return nil, nil, err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 64<<10), 2<<20)
	seen := make(map[string]bool, expected)
	operations := make(map[string]json.RawMessage)
	var rows []json.RawMessage
	var packs []Pack
	count := 0
	flush := func() error {
		if len(rows) == 0 {
			return nil
		}
		object, err := store.JSON(rows)
		if err != nil {
			return err
		}
		packs = append(packs, Pack{Rows: len(rows), Object: object})
		rows = nil
		return nil
	}
	for scanner.Scan() {
		identity, _, row, err := decodeRow(scanner.Bytes())
		if err != nil {
			return nil, nil, err
		}
		if seen[identity] {
			return nil, nil, fmt.Errorf("duplicate metadata id %s", identity)
		}
		seen[identity] = true
		if drawPlans {
			values, ok := row["operations"].([]any)
			if !ok {
				return nil, nil, errors.New("draw plan lacks operations")
			}
			refs := make([]string, 0, len(values))
			for _, value := range values {
				op, ok := value.(map[string]any)
				if !ok {
					return nil, nil, errors.New("invalid draw operation")
				}
				if _, ok := op["assetId"].(string); !ok {
					return nil, nil, errors.New("draw operation lacks texture identity")
				}
				canonical, err := json.Marshal(op)
				if err != nil {
					return nil, nil, err
				}
				sum := sha256.Sum256(canonical)
				key := hex.EncodeToString(sum[:])
				if _, exists := operations[key]; !exists {
					operations[key] = canonical
				}
				refs = append(refs, key)
			}
			delete(row, "operations")
			row["operationIds"] = refs
		}
		canonical, err := json.Marshal(row)
		if err != nil {
			return nil, nil, err
		}
		rows = append(rows, canonical)
		count++
		if count > expected {
			return nil, nil, errors.New("metadata count exceeds declaration")
		}
		if len(rows) == 256 {
			if err := flush(); err != nil {
				return nil, nil, err
			}
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, nil, err
	}
	if count != expected {
		return nil, nil, errors.New("metadata count mismatch")
	}
	if err := flush(); err != nil {
		return nil, nil, err
	}
	keys := make([]string, 0, len(operations))
	for key := range operations {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var opPacks []Pack
	var group []map[string]any
	prefix := ""
	flushOps := func() error {
		if len(group) == 0 {
			return nil
		}
		object, err := store.JSON(group)
		if err != nil {
			return err
		}
		opPacks = append(opPacks, Pack{Rows: len(group), Key: prefix, Object: object})
		group = nil
		return nil
	}
	for _, key := range keys {
		if key[:2] != prefix {
			if err := flushOps(); err != nil {
				return nil, nil, err
			}
			prefix = key[:2]
		}
		group = append(group, map[string]any{"id": key, "operation": operations[key]})
	}
	if err := flushOps(); err != nil {
		return nil, nil, err
	}
	return packs, opPacks, nil
}
