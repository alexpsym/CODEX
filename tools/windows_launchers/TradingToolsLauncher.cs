using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

internal static class Program
{
    private const string TargetBat = "__TARGET_BAT__";

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

            ProcessStartInfo startInfo = new ProcessStartInfo();
            startInfo.FileName = "cmd.exe";
            startInfo.Arguments = cmdArguments;
            startInfo.WorkingDirectory = workingDirectory;
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;
            startInfo.WindowStyle = ProcessWindowStyle.Hidden;

            using (Process process = Process.Start(startInfo))
            {
                if (process == null)
                {
                    ShowError("Failed to launch cmd.exe for target batch file.");
                    return 1;
                }

                process.WaitForExit();
                int exitCode = process.ExitCode;
                if (exitCode != 0)
                {
                    ShowError("Launcher preflight failed with exit code " + exitCode + ".\n\nCheck the Local Master Control window for startup errors.");
                }

                return exitCode;
            }
        }
        catch (Exception ex)
        {
            ShowError("Failed to launch target batch file:\n" + ex.Message);
            return 1;
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
