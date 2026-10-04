package derived

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type scratchWriter struct {
	t         *testing.T
	dir       string
	prefix    string
	files     int
	failure   error
	inspected bool
}

func (w *scratchWriter) Write(data []byte) (int, error) {
	if !w.inspected {
		entries, err := os.ReadDir(w.dir)
		if err != nil {
			w.t.Fatal(err)
		}
		if len(entries) != w.files {
			w.t.Fatalf("scratch files=%d, want %d", len(entries), w.files)
		}
		for _, entry := range entries {
			if !strings.HasPrefix(entry.Name(), w.prefix) {
				w.t.Fatalf("unexpected scratch: %s", entry.Name())
			}
			file, err := os.Open(filepath.Join(w.dir, entry.Name()))
			if err != nil {
				w.t.Fatal(err)
			}
			info, err := file.Stat()
			file.Close()
			if err != nil {
				w.t.Fatal(err)
			}
			if !info.Mode().IsRegular() || info.Size() == 0 {
				w.t.Fatalf("empty/nonregular scratch: %s", entry.Name())
			}
		}
		w.inspected = true
	}
	if w.failure != nil {
		return 0, w.failure
	}
	return len(data), nil
}

func TestDerivedUsesPrivateScratchAndCleansUp(t *testing.T) {
	for _, test := range []struct {
		name, prefix string
		files        int
		build        func([]Candidate, io.Writer, string) error
	}{
		{"SRGB", "srgb-", 2, BuildSRGB},
		{"TXCI", "txci-", 1, BuildTXCI},
	} {
		for _, fail := range []bool{false, true} {
			name := test.name + "/success"
			if fail {
				name = test.name + "/writer-error"
			}
			t.Run(name, func(t *testing.T) {
				root := t.TempDir()
				private := filepath.Join(root, ".private")
				if err := os.Mkdir(private, 0700); err != nil {
					t.Fatal(err)
				}
				// Any fallback to the system temporary directory must fail. A file
				// cannot be used as a temp directory on either Windows or Linux.
				blocked := filepath.Join(root, "blocked-system-tmp")
				if err := os.WriteFile(blocked, []byte("file"), 0600); err != nil {
					t.Fatal(err)
				}
				for _, key := range []string{"TMPDIR", "TMP", "TEMP"} {
					t.Setenv(key, blocked)
				}
				failure := errors.New("injected destination write failure")
				writer := &scratchWriter{t: t, dir: private, prefix: test.prefix, files: test.files}
				if fail {
					writer.failure = failure
				}
				err := test.build([]Candidate{{Stable: true}}, writer, private)
				if fail {
					if !errors.Is(err, failure) {
						t.Fatalf("expected write failure: %v", err)
					}
				} else if err != nil {
					t.Fatal(err)
				}
				if !writer.inspected {
					t.Fatal("destination writer was not reached")
				}
				entries, err := os.ReadDir(private)
				if err != nil || len(entries) != 0 {
					t.Fatalf("scratch not cleaned: %v %v", entries, err)
				}
				entries, err = os.ReadDir(root)
				if err != nil || len(entries) != 2 {
					t.Fatalf("scratch escaped private directory: %v %v", entries, err)
				}
			})
		}
	}
}

func TestDerivedRequiresScratchDirectory(t *testing.T) {
	for _, build := range []func([]Candidate, io.Writer, string) error{BuildSRGB, BuildTXCI} {
		if err := build([]Candidate{{Stable: true}}, io.Discard, ""); err == nil {
			t.Fatal("empty scratch path silently fell back to system tmp")
		}
	}
}
