package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"terraria-resource-pipeline/internal/pipeline"
)

func run(ctx context.Context, args []string) (int, error) {
	if len(args) == 0 {
		return 2, errors.New("usage: trp extract|verify|snapshot|review|prepare-publication [options]")
	}
	flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
	input := flags.String("input", "", "uploaded ZIP")
	output := flags.String("output", "", "new output attempt directory")
	candidate := flags.String("candidate", "", "candidate directory")
	baseline := flags.String("baseline", "", "completed public resource snapshot directory")
	sourceURL := flags.String("source-url", "", "public backend resource proxy endpoint")
	reviewPath := flags.String("review", "", "private review JSON file")
	approval := flags.String("approve-review-sha256", "", "administrator-confirmed review hash")
	memoryPath := flags.String("memory-report", "", "private memory report path for verify/review/publish")
	image := flags.String("runtime-image", "terraria-runtime-extractor:local", "configured runtime image (outside task container)")
	timeout := flags.Duration("timeout", 30*time.Minute, "maximum extraction time")
	if err := flags.Parse(args[1:]); err != nil {
		return 2, err
	}
	if flags.NArg() != 0 {
		return 2, errors.New("unexpected positional arguments")
	}
	measure := func(stage string, operation func() error) error {
		report, err := pipeline.Measure(stage, operation)
		json.NewEncoder(os.Stderr).Encode(map[string]any{"event": "memory", "memory": report})
		if *memoryPath != "" {
			data, encodeErr := json.Marshal(report)
			if encodeErr != nil {
				return errors.Join(err, encodeErr)
			}
			if saveErr := os.WriteFile(*memoryPath, append(data, '\n'), 0600); saveErr != nil {
				return errors.Join(err, saveErr)
			}
		}
		return err
	}
	switch args[0] {
	case "publish", "publication-status":
		return 2, errors.New("Git publication removed; use viewer-boot AList review and publication")
	case "snapshot":
		if *sourceURL == "" || *output == "" {
			return 2, errors.New("snapshot requires --source-url and --output")
		}
		ctx, cancel := context.WithTimeout(ctx, *timeout)
		defer cancel()
		err := measure("snapshot", func() error {
			identity, err := pipeline.Snapshot(ctx, *sourceURL, *output)
			if err != nil {
				return err
			}
			return json.NewEncoder(os.Stdout).Encode(identity)
		})
		return 0, err
	case "extract":
		if *input == "" || *output == "" {
			return 2, errors.New("extract requires --input and --output")
		}
		ctx, cancel := context.WithTimeout(ctx, *timeout)
		defer cancel()
		report, err := pipeline.Extract(ctx, pipeline.Options{Input: *input, Output: *output, RuntimeImage: *image, Progress: func(stage string) {
			json.NewEncoder(os.Stderr).Encode(map[string]any{"event": "stage", "stage": stage})
		}})
		json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "result", "status": report.Status, "manifestSha256": report.ManifestSHA256, "report": filepath.Join(*output, ".private", "report.json"), "memory": report.Memory, "error": report.Error})
		if err != nil {
			return 1, err
		}
		if report.Status == "incomplete" {
			return 4, nil
		}
		return 0, nil
	case "verify":
		if *candidate == "" {
			return 2, errors.New("verify requires --candidate")
		}
		return 0, measure("verify", func() error { return pipeline.Verify(*candidate) })
	case "review", "diff":
		if *candidate == "" || *baseline == "" || *output == "" {
			return 2, errors.New("review requires --candidate, --baseline and --output review file")
		}
		err := measure("review", func() error {
			review, sha, err := pipeline.WriteReview(*candidate, *baseline, *output)
			if err != nil {
				return err
			}
			return json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "review", "reviewSha256": sha, "candidateManifestSha256": review.CandidateManifestSHA256, "baselineManifestSha256": review.BaselineManifestSHA256, "baselineChannelSha256": review.BaselineChannelSHA256, "missing": review.Missing})
		})
		return 0, err
	case "prepare-publication":
		if *candidate == "" || *baseline == "" || *reviewPath == "" || *approval == "" || *output == "" {
			return 2, errors.New("prepare-publication requires --candidate, --baseline, --review, --approve-review-sha256 and --output")
		}
		err := measure("prepare-publication", func() error {
			plan, err := pipeline.PreparePublication(*candidate, *baseline, *reviewPath, *approval, *output)
			if err != nil {
				return err
			}
			return json.NewEncoder(os.Stdout).Encode(plan)
		})
		return 0, err
	default:
		return 2, fmt.Errorf("unknown command %q", args[0])
	}
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	code, err := run(ctx, os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		if code == 0 {
			code = 1
		}
	}
	os.Exit(code)
}
