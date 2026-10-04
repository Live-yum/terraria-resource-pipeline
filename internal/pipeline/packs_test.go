package pipeline

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"terraria-resource-pipeline/internal/artifact"
)

func TestPacksPreserveLargeIntegerAttributes(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	name := filepath.Join(root, "large.ndjson")
	if err := os.WriteFile(name, []byte("{\"id\":1,\"mask\":18446744073709551615}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	packs, _, err := packFamily(store, name, 1, false)
	if err != nil {
		t.Fatal(err)
	}
	var raw json.RawMessage
	if err := ReadObject(root, packs[0].Object, &raw); err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(raw, []byte("18446744073709551615")) {
		t.Fatalf("integer precision lost: %s", raw)
	}
}

func TestPlansDeduplicateWithoutLosingOrderOrGeometry(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	name := filepath.Join(root, "plans.ndjson")
	data := `{"id":"base.0.frame.0","operations":[{"assetId":"Player_0_3","layer":0,"rotation":0.125,"shader":2,"requestedSourceRect":null}]}` + "\n" + `{"id":"hair.1.frame.0","operations":[{"shader":2,"layer":0,"assetId":"Player_0_3","rotation":0.125,"requestedSourceRect":null},{"assetId":"Hair_1","layer":1,"rotation":0.125,"shader":0}]}` + "\n"
	if err := os.WriteFile(name, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	plans, ops, err := packFamily(store, name, 2, true)
	if err != nil {
		t.Fatal(err)
	}
	var records []struct {
		ID           string
		OperationIDs []string `json:"operationIds"`
	}
	if err := ReadObject(root, plans[0].Object, &records); err != nil {
		t.Fatal(err)
	}
	if len(records[1].OperationIDs) != 2 || records[0].OperationIDs[0] != records[1].OperationIDs[0] {
		t.Fatal("lost operation order or deduplication")
	}
	count := 0
	for _, pack := range ops {
		var rows []struct {
			ID        string
			Operation json.RawMessage
		}
		if err := ReadObject(root, pack.Object, &rows); err != nil {
			t.Fatal(err)
		}
		count += len(rows)
		for _, row := range rows {
			if row.ID[:2] != pack.Key {
				t.Fatal("bad operation shard key")
			}
		}
	}
	if count != 2 {
		t.Fatalf("wanted 2 unique ops, got %d", count)
	}
}
