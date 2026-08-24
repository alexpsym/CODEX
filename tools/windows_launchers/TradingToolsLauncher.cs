using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Drawing;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const string TargetBat = "__TARGET_BAT__";
    private static readonly object LogFileLock = new object();

    [STAThread]
    public static int Main(string[] args)
    {
        try
        {
            string targetBatPath = ResolveTargetBatPath(TargetBat);
            if (targetBatPath == null)
            {
                ShowError("Could not find required launcher target '" + TargetBat + "'.\n\nLooked relative to the launcher location and current working directory.");
                return 1;
            }

            string cmdArguments = "/d /s /c \"\"" + targetBatPath + "\"" + BuildForwardedArgString(args) + "\"";
            string workingDirectory = Path.GetDirectoryName(targetBatPath);
            if (string.IsNullOrEmpty(workingDirectory))
            {
                workingDirectory = Environment.CurrentDirectory;
            }
            string launchLogPath = Path.Combine(workingDirectory, "logs", "LocalTradingTools-launch-latest.log");
            string workerLogPath = Path.Combine(workingDirectory, "logs", "LocalTradingTools-worker-latest.log");
            LogTail logTail = new LogTail(18);
            InitializeLog(launchLogPath, targetBatPath, cmdArguments);

            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = "cmd.exe";
            startInfo.Arguments = cmdArguments;
            startInfo.WorkingDirectory = workingDirectory;
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;
            startInfo.WindowStyle = ProcessWindowStyle.Hidden;
            startInfo.RedirectStandardOutput = true;
            startInfo.RedirectStandardError = true;

            using (Process process = new Process())
            {
                process.StartInfo = startInfo;
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
                {
                    AppendLogLine(launchLogPath, logTail, eventArgs.Data, false);
                };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs eventArgs)
                {
                    AppendLogLine(launchLogPath, logTail, eventArgs.Data, true);
                };
                if (!process.Start())
                {
                    ShowError("Failed to launch cmd.exe for target batch file.\n\nLog: " + launchLogPath);
                    return 1;
                }
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                // The visible worker intentionally outlives this readiness batch
                // and can inherit the redirected pipe handles. The parameterless
                // overload also waits for those pipes to close, which would delay
                // the ready notification until the backend shuts down.
                while (!process.WaitForExit(250))
                {
                }
                int exitCode = process.ExitCode;
                TryCancelAsyncRead(process, true);
                TryCancelAsyncRead(process, false);
                AppendLogLine(launchLogPath, logTail, "Launcher target exited with code " + exitCode + ".", false);
                if (exitCode != 0)
                {
                    ShowError(BuildFailureMessage(exitCode, launchLogPath, logTail.ToString(), workerLogPath));
                    return exitCode;
                }

                TryShowReadyNotification(launchLogPath, logTail);
                return 0;
            }
        }
        catch (Exception ex)
        {
            ShowError("Failed to launch target batch file:\n" + ex.Message);
            return 1;
        }
    }

    private static void TryCancelAsyncRead(Process process, bool standardOutput)
    {
        try
        {
            if (standardOutput)
            {
                process.CancelOutputRead();
            }
            else
            {
                process.CancelErrorRead();
            }
        }
        catch (InvalidOperationException)
        {
        }
    }

    private static void TryShowReadyNotification(string launchLogPath, LogTail logTail)
    {
        try
        {
            Icon executableIcon = null;
            try
            {
                try
                {
                    executableIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                }
                catch
                {
                }

                using (NotifyIcon notification = new NotifyIcon())
                {
                    notification.Icon = executableIcon ?? SystemIcons.Application;
                    notification.BalloonTipTitle = "Local Trading Tools";
                    notification.BalloonTipText = "Local Trading Tools is ready to use.";
                    notification.BalloonTipIcon = ToolTipIcon.Info;
                    notification.Text = "Local Trading Tools";
                    notification.Visible = true;
                    notification.ShowBalloonTip(5000);
                    Application.DoEvents();
                    Thread.Sleep(5000);
                    Application.DoEvents();
                    notification.Visible = false;
                }
            }
            finally
            {
                if (executableIcon != null)
                {
                    executableIcon.Dispose();
                }
            }
            AppendLogLine(launchLogPath, logTail, "Ready notification displayed.", false);
        }
        catch (Exception ex)
        {
            AppendLogLine(launchLogPath, logTail, "Ready notification failed: " + ex.Message, true);
        }
    }

    private static void InitializeLog(string logPath, string targetBatPath, string cmdArguments)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(logPath));
            string header =
                "Local Trading Tools launcher log" + Environment.NewLine +
                "Started: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + Environment.NewLine +
                "Target BAT: " + targetBatPath + Environment.NewLine +
                "Arguments: " + cmdArguments + Environment.NewLine +
                Environment.NewLine;
            File.WriteAllText(logPath, header, Encoding.UTF8);
        }
        catch
        {
        }
    }

    private static void AppendLogLine(string logPath, LogTail logTail, string line, bool isError)
    {
        if (line == null)
        {
            return;
        }

        string prefix = isError ? "[stderr] " : "";
        string text = prefix + line;
        try
        {
            lock (LogFileLock)
            {
                File.AppendAllText(logPath, text + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch
        {
        }

        logTail.Add(text);
    }

    private static string BuildFailureMessage(int exitCode, string launchLogPath, string launchLogTail, string workerLogPath)
    {
        string message =
            "Launcher preflight failed with exit code " + exitCode + ".\n\n" +
            "Log: " + launchLogPath + "\n\n" +
            "Last useful launch log lines:\n" + launchLogTail;

        string workerLogTail = ReadUsefulLogTail(workerLogPath, 18);
        if (string.IsNullOrEmpty(workerLogTail))
        {
            workerLogTail = "(no worker log output captured)";
        }
        message +=
            "\n\nWorker log: " + workerLogPath + "\n\n" +
            "Last useful worker log lines:\n" + workerLogTail;

        return message;
    }

    private static string ReadUsefulLogTail(string logPath, int limit)
    {
        try
        {
            if (!File.Exists(logPath))
            {
                return "";
            }

            Queue<string> tail = new Queue<string>();
            foreach (string rawLine in File.ReadAllLines(logPath, Encoding.UTF8))
            {
                if (string.IsNullOrWhiteSpace(rawLine))
                {
                    continue;
                }
                tail.Enqueue(rawLine.Trim());
                while (tail.Count > limit)
                {
                    tail.Dequeue();
                }
            }

            if (tail.Count == 0)
            {
                return "";
            }
            return string.Join(Environment.NewLine, tail.ToArray());
        }
        catch
        {
            return "";
        }
    }

    private sealed class LogTail
    {
        private readonly int limit;
        private readonly Queue<string> lines = new Queue<string>();

        public LogTail(int limit)
        {
            this.limit = Math.Max(1, limit);
        }

        public void Add(string line)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                return;
            }
            lock (lines)
            {
                lines.Enqueue(line.Trim());
                while (lines.Count > limit)
                {
                    lines.Dequeue();
                }
            }
        }

        public override string ToString()
        {
            lock (lines)
            {
                if (lines.Count == 0)
                {
                    return "(no log output captured)";
                }
                return string.Join(Environment.NewLine, lines.ToArray());
            }
        }
    }

    private static void ShowError(string message)
    {
        string fullMessage = "Local Trading Tools launcher error\n\n" + message;
        try
        {
            MessageBox.Show(fullMessage, "Local Trading Tools", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
        catch
        {
            Console.Error.WriteLine(fullMessage);
        }
    }

    private static string ResolveTargetBatPath(string targetBat)
    {
        string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
        string currentDirectory = Environment.CurrentDirectory;

        List<string> candidates = new List<string>();
        candidates.Add(Path.Combine(baseDirectory, targetBat));
        candidates.Add(Path.Combine(baseDirectory, "..", targetBat));
        candidates.Add(Path.Combine(baseDirectory, "..", "..", targetBat));
        candidates.Add(Path.Combine(currentDirectory, targetBat));

        foreach (string rawCandidate in candidates)
        {
            string candidate = Path.GetFullPath(rawCandidate);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private static string BuildForwardedArgString(string[] args)
    {
        if (args == null || args.Length == 0)
        {
            return string.Empty;
        }

        return " " + string.Join(" ", args.Select(QuoteForCmd).ToArray());
    }

    private static string QuoteForCmd(string arg)
    {
        if (arg == null || arg.Length == 0)
        {
            return "\"\"";
        }

        bool needsQuotes = arg.Any(char.IsWhiteSpace) || arg.IndexOf('"') >= 0;
        if (!needsQuotes)
        {
            return arg;
        }

        return "\"" + arg.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
