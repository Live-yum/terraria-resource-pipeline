package pipeline

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"terraria-resource-pipeline/internal/artifact"
)

func TestRowsRejectIdentityAndCountCorruption(t *testing.T) {
	name := filepath.Join(t.TempDir(), "rows.ndjson")
	for _, test := range []struct {
		data  string
		count int
		valid bool
	}{
		{"{\"id\":1,\"a\":2}\n{\"id\":2}\n", 2, true},
		{"{\"id\":1}\n{\"id\":1}\n", 2, false},
		{"{\"id\":1}\n", 2, false},
		{"{\"a\":1}\n", 1, false},
	} {
		if err := os.WriteFile(name, []byte(test.data), 0600); err != nil {
			t.Fatal(err)
		}
		_, err := readRows(name, test.count)
		if (err == nil) != test.valid {
			t.Fatalf("data=%q count=%d error=%v", test.data, test.count, err)
		}
	}
}

func TestPartialHelperCannotClaimCompleteResources(t *testing.T) {
	partial := RuntimeResult{Protocol: 1, GameVersion: "future", Families: map[string]RuntimeFamily{
		"ids": {Count: 1}, "items": {Count: 1}, "pixel-candidates": {Count: 1},
	}}
	if err := validateRuntimeCoverage(partial); err == nil {
		t.Fatal("accepted helper with skipped client domains")
	}
}

func TestFailedExtractionKeepsMemoryReport(t *testing.T) {
	output := filepath.Join(t.TempDir(), "attempt")
	_, err := Extract(context.Background(), Options{Input: "missing.zip", Output: output})
	if err == nil {
		t.Fatal("accepted missing archive")
	}
	data, err := os.ReadFile(filepath.Join(output, ".private", "report.json"))
	if err != nil {
		t.Fatal(err)
	}
	var report Report
	if err := json.Unmarshal(data, &report); err != nil {
		t.Fatal(err)
	}
	if report.Status != "failed" || report.Error == "" || len(report.Memory.Stages) != 1 || report.Memory.Stages[0].Stage != "unpack" || report.Memory.TargetBytes != 300000000 {
		t.Fatalf("missing failure diagnostics: %+v", report)
	}
}

func TestItemDetailsShareNamesAndMergeTooltip(t *testing.T) {
	root := t.TempDir()
	store, err := artifact.New(root)
	if err != nil {
		t.Fatal(err)
	}
	name := filepath.Join(root, "tooltip.ndjson")
	if err := os.WriteFile(name, []byte("{\"id\":1,\"zh-Hans\":[\"说明\"],\"en-US\":[\"tip\"]}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "bindings.ndjson"), []byte("{\"id\":\"Item:1\",\"kind\":\"Item\",\"numericId\":1,\"assetId\":\"Item_7\",\"animation\":{\"type\":\"Terraria.DataStructures.DrawAnimationVertical\",\"frameCount\":8},\"sourceRect\":[0,0,22,24]}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "research.ndjson"), []byte("{\"id\":1,\"required\":1,\"persistentItemId\":2,\"persistentId\":\"OfficialAlias\"}\n"), 0600); err != nil {
		t.Fatal(err)
	}
	packs, index, err := exportItems(store, []json.RawMessage{json.RawMessage(`{"id":1,"internalName":"IronPickaxe","name":{"zh-Hans":"铁镐"},"gameplay":{"damage":5},"research":1}`)}, root, RuntimeFamily{Path: "research.ndjson", Count: 1}, RuntimeFamily{Path: "tooltip.ndjson", Count: 1}, RuntimeFamily{}, RuntimeFamily{Path: "bindings.ndjson", Count: 1})
	if err != nil {
		t.Fatal(err)
	}
	var details []map[string]any
	if err := ReadObject(root, packs[0].Object, &details); err != nil {
		t.Fatal(err)
	}
	if _, ok := details[0]["name"]; ok {
		t.Fatal("duplicate name in item detail")
	}
	if details[0]["tooltip"] == nil || details[0]["gameplay"] == nil {
		t.Fatal("lost tooltip or attributes")
	}
	var entries []map[string]any
	if err := ReadObject(root, index.Object, &entries); err != nil {
		t.Fatal(err)
	}
	if entries[0]["name"] == nil || entries[0]["texture"] != "Item_7" || entries[0]["persistentId"] != "OfficialAlias" {
		t.Fatal("lost shared item identity")
	}
	if frame, ok := entries[0]["sourceRect"].([]any); !ok || len(frame) != 4 || frame[2] != float64(22) || frame[3] != float64(24) {
		t.Fatalf("animated item frame absent from index: %v", entries[0]["sourceRect"])
	}
}
