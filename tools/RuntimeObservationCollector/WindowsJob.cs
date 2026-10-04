using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

namespace RuntimeObservationCollector {
// Resource containment only. Network/host-filesystem/desktop isolation MUST be
// established outside this process. No command-line flag creates that boundary.
internal static class WindowsJob {
#pragma warning disable 0649 // Native-layout fields are initialized through the enclosing struct.
    [StructLayout(LayoutKind.Sequential)] private struct Basic {
        internal long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        internal uint LimitFlags;
        internal UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        internal uint ActiveProcessLimit;
        internal UIntPtr Affinity;
        internal uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Io {
        internal ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        internal ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)] private struct Extended {
        internal Basic BasicLimitInformation;
        internal Io IoInfo;
        internal UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetInformationJobObject(IntPtr job, int type, ref Extended info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    #pragma warning restore 0649
    private static IntPtr jobHandle;
    private static Timer timer;
    internal static void Install() {
        if (Environment.OSVersion.Platform != PlatformID.Win32NT || IntPtr.Size != 4)
            throw new InvalidOperationException("WINDOWS_X86_REQUIRED");
        jobHandle = CreateJobObject(IntPtr.Zero, null);
        if (jobHandle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
        Extended e = new Extended();
        e.BasicLimitInformation.LimitFlags = 0x2000 | 0x0008 | 0x0100 | 0x0002;
        e.BasicLimitInformation.ActiveProcessLimit = 1;
        e.BasicLimitInformation.PerProcessUserTimeLimit = 60L * 10000000L;
        e.ProcessMemoryLimit = new UIntPtr(1024U * 1024U * 1024U);
        if (!SetInformationJobObject(jobHandle, 9, ref e, (uint)Marshal.SizeOf(typeof(Extended))) ||
            !AssignProcessToJobObject(jobHandle, Process.GetCurrentProcess().Handle))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        Stopwatch elapsed = Stopwatch.StartNew();
        timer = new Timer(delegate(object ignored) {
            using (Process process = Process.GetCurrentProcess()) {
                if (elapsed.ElapsedMilliseconds > 120000 || process.Threads.Count > 64)
                    Environment.Exit(124);
            }
        }, null, 250, 250);
        GC.KeepAlive(timer);
    }
}}
