package artifact

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
)

func TestDeterminismAndIntegrity(t *testing.T) {
	store, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	one, err := store.JSON(map[string]any{"b": 2, "a": "中文"})
	if err != nil {
		t.Fatal(err)
	}
	two, err := store.JSON(map[string]any{"a": "中文", "b": 2})
	if err != nil {
		t.Fatal(err)
	}
	if one != two {
		t.Fatal("identical JSON produced different objects")
	}
	if err := Verify(store.Root, one); err != nil {
		t.Fatal(err)
	}
	wrong := one
	wrong.DecodedBytes--
	if Verify(store.Root, wrong) == nil {
		t.Fatal("accepted wrong decoded size")
	}
	wrong = one
	wrong.Path = "../outside"
	if Verify(store.Root, wrong) == nil {
		t.Fatal("accepted traversal")
	}
	filename := filepath.Join(store.Root, filepath.FromSlash(one.Path))
	bytes, err := os.ReadFile(filename)
	if err != nil {
		t.Fatal(err)
	}
	bytes[len(bytes)-1] ^= 1
	if err := os.WriteFile(filename, bytes, 0600); err != nil {
		t.Fatal(err)
	}
	if Verify(store.Root, one) == nil {
		t.Fatal("accepted tampered object")
	}
	if _, err := store.JSON(map[string]any{"a": "中文", "b": 2}); err == nil {
		t.Fatal("silently replaced corrupt existing object")
	}
}

func TestWriterFailure(t *testing.T) {
	store, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.Put(".bin", "application/octet-stream", func(writer io.Writer) error {
		writer.Write([]byte("partial"))
		return errors.New("failed")
	}); err == nil {
		t.Fatal("ignored writer failure")
	}
	files, err := filepath.Glob(filepath.Join(store.Root, ".object-*"))
	if err != nil || len(files) != 0 {
		t.Fatal("left temporary objects")
	}
}
