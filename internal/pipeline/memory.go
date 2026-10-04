package pipeline

import (
	"os"
	"path/filepath"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"sync"
	"time"
)

type MemoryStage struct {
	Stage           string `json:"stage"`
	Milliseconds    int64  `json:"milliseconds"`
	PeakRSSBytes    uint64 `json:"peakRssBytes,omitempty"`
	PeakHeapBytes   uint64 `json:"peakGoHeapBytes"`
	CgroupPeakBytes uint64 `json:"cgroupCumulativePeakBytes,omitempty"`
}

type MemoryReport struct {
	Measurement     string        `json:"measurement"`
	TargetBytes     uint64        `json:"targetBytes"`
	PeakRSSBytes    uint64        `json:"peakRssBytes,omitempty"`
	CgroupPeakBytes uint64        `json:"cgroupPeakBytes,omitempty"`
	Stages          []MemoryStage `json:"stages"`
}

type memoryMonitor struct {
	mu      sync.Mutex
	current MemoryStage
	started time.Time
	report  MemoryReport
	stop    chan struct{}
	done    chan struct{}
}

// Measure also covers trusted review/publish commands and their Git children.
func Measure(stage string, operation func() error) (report MemoryReport, err error) {
	monitor := newMemoryMonitor()
	limit := debug.SetMemoryLimit(220000000)
	monitor.phase(stage)
	defer func() {
		report = monitor.finish()
		debug.SetMemoryLimit(limit)
	}()
	err = operation()
	return
}

func newMemoryMonitor() *memoryMonitor {
	m := &memoryMonitor{stop: make(chan struct{}), done: make(chan struct{}), report: MemoryReport{TargetBytes: 300000000}}
	if runtime.GOOS == "linux" {
		m.report.Measurement = "Linux /proc aggregate RSS sampled every 50ms; kernel cgroup memory.peak includes charged cache"
	} else {
		m.report.Measurement = "Go heap only on this host; full RSS acceptance requires Linux container run"
	}
	go func() {
		defer close(m.done)
		tick := time.NewTicker(50 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-tick.C:
				m.sample()
			case <-m.stop:
				return
			}
		}
	}()
	return m
}

func processRSS(pid int, seen map[int]bool) uint64 {
	if seen[pid] {
		return 0
	}
	seen[pid] = true
	base := filepath.Join("/proc", strconv.Itoa(pid))
	status, err := os.ReadFile(filepath.Join(base, "status"))
	if err != nil {
		return 0
	}
	var total uint64
	for _, line := range strings.Split(string(status), "\n") {
		if strings.HasPrefix(line, "VmRSS:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				total, _ = strconv.ParseUint(fields[1], 10, 64)
				total *= 1024
			}
			break
		}
	}
	tasks, _ := os.ReadDir(filepath.Join(base, "task"))
	for _, task := range tasks {
		children, _ := os.ReadFile(filepath.Join(base, "task", task.Name(), "children"))
		for _, child := range strings.Fields(string(children)) {
			if id, err := strconv.Atoi(child); err == nil {
				total += processRSS(id, seen)
			}
		}
	}
	return total
}

func cgroupPeak() uint64 {
	for _, filename := range []string{"/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"} {
		if bytes, err := os.ReadFile(filename); err == nil {
			if value, err := strconv.ParseUint(strings.TrimSpace(string(bytes)), 10, 64); err == nil {
				return value
			}
		}
	}
	return 0
}

func (m *memoryMonitor) sample() {
	var mem runtime.MemStats
	runtime.ReadMemStats(&mem)
	var rss uint64
	if runtime.GOOS == "linux" {
		rss = processRSS(os.Getpid(), make(map[int]bool))
	}
	peak := cgroupPeak()
	m.mu.Lock()
	defer m.mu.Unlock()
	m.current.PeakRSSBytes = max(m.current.PeakRSSBytes, rss)
	m.current.PeakHeapBytes = max(m.current.PeakHeapBytes, mem.HeapAlloc)
	m.current.CgroupPeakBytes = max(m.current.CgroupPeakBytes, peak)
	m.report.PeakRSSBytes = max(m.report.PeakRSSBytes, rss)
	m.report.CgroupPeakBytes = max(m.report.CgroupPeakBytes, peak)
}

func (m *memoryMonitor) phase(name string) {
	m.sample()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.current.Stage != "" {
		m.current.Milliseconds = time.Since(m.started).Milliseconds()
		m.report.Stages = append(m.report.Stages, m.current)
	}
	m.current = MemoryStage{Stage: name}
	m.started = time.Now()
}

func (m *memoryMonitor) finish() MemoryReport {
	close(m.stop)
	<-m.done
	m.sample()
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.current.Stage != "" {
		m.current.Milliseconds = time.Since(m.started).Milliseconds()
		m.report.Stages = append(m.report.Stages, m.current)
	}
	return m.report
}
