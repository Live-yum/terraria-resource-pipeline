package input

import (
	"archive/zip"
	"context"
	"os"
	"path/filepath"
	"testing"
)

func archive(t *testing.T, names []string) string {
	t.Helper()
	filename := filepath.Join(t.TempDir(), "input.zip")
	f, err := os.Create(filename)
	if err != nil {
		t.Fatal(err)
	}
	w := zip.NewWriter(f)
	for _, name := range names {
		entry, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write([]byte("hello")); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	return filename
}

func TestExtractZIP(t *testing.T) {
	source := archive(t, []string{"game/TerrariaServer.exe", "Content/Images/Item_1.xnb", "Content/Wave Bank.xwb"})
	destination := filepath.Join(t.TempDir(), "new")
	result, err := ExtractZIP(context.Background(), source, destination, DefaultLimits())
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Files) != 3 || result.ExpandedBytes != 10 || result.Files[2].Skipped == "" || len(result.ArchiveSHA256) != 64 {
		t.Fatalf("bad inventory: %+v", result)
	}
	if _, err := os.Stat(filepath.Join(destination, "Content/Wave Bank.xwb")); !os.IsNotExist(err) {
		t.Fatal("music bank was expanded")
	}
	if _, err := ExtractZIP(context.Background(), source, destination, DefaultLimits()); err == nil {
		t.Fatal("overwrote existing output")
	}
}

func TestRejectUnsafeArchive(t *testing.T) {
	for _, names := range [][]string{{"../escape"}, {"/absolute"}, {"a\\b"}, {"C:/drive"}, {"CON.txt"}, {"foo", "FOO"}, {"a", "a/b"}} {
		t.Run(names[0], func(t *testing.T) {
			destination := filepath.Join(t.TempDir(), "new")
			if _, err := ExtractZIP(context.Background(), archive(t, names), destination, DefaultLimits()); err == nil {
				t.Fatal("accepted unsafe paths")
			}
			if _, err := os.Stat(destination); !os.IsNotExist(err) {
				t.Fatal("left output after failure")
			}
		})
	}
	limits := DefaultLimits()
	limits.FileBytes = 4
	if _, err := ExtractZIP(context.Background(), archive(t, []string{"large"}), filepath.Join(t.TempDir(), "new"), limits); err == nil {
		t.Fatal("accepted oversized entry")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := ExtractZIP(ctx, archive(t, []string{"file"}), filepath.Join(t.TempDir(), "new"), DefaultLimits()); err == nil {
		t.Fatal("ignored cancellation")
	}
}
