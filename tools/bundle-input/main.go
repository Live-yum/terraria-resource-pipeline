// bundle-input is a development fixture packer, not part of deployed extraction.
package main

import (
	"archive/zip"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 4 {
		panic("usage: go run ./tools/bundle-input output.zip server-directory Content-directory")
	}
	out, err := os.OpenFile(os.Args[1], os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		panic(err)
	}
	zipper := zip.NewWriter(out)
	for i, root := range os.Args[2:] {
		prefix := "server"
		if i == 1 {
			prefix = "Content"
		}
		err = filepath.WalkDir(root, func(name string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() {
				return nil
			}
			if entry.Type()&os.ModeSymlink != 0 {
				return fmt.Errorf("fixture contains symlink %s", name)
			}
			rel, err := filepath.Rel(root, name)
			if err != nil {
				return err
			}
			info, err := entry.Info()
			if err != nil {
				return err
			}
			header, err := zip.FileInfoHeader(info)
			if err != nil {
				return err
			}
			header.Name = prefix + "/" + filepath.ToSlash(rel)
			header.Method = zip.Deflate
			writer, err := zipper.CreateHeader(header)
			if err != nil {
				return err
			}
			file, err := os.Open(name)
			if err != nil {
				return err
			}
			_, err = io.Copy(writer, file)
			file.Close()
			return err
		})
		if err != nil {
			panic(err)
		}
	}
	if err := zipper.Close(); err != nil {
		panic(err)
	}
	if err := out.Close(); err != nil {
		panic(err)
	}
}
