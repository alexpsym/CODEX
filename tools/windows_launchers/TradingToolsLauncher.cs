using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;

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
                Console.Error.WriteLine("ERROR: Could not find required launcher target '" + TargetBat + "'.");
                Console.Error.WriteLine("Looked relative to the launcher location and current working directory.");
                return 1;
            }

            Console.WriteLine("Launching " + targetBatPath);

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

            using (Process process = Process.Start(startInfo))
            {
                if (process == null)
                {
                    Console.Error.WriteLine("ERROR: Failed to launch cmd.exe for target batch file.");
                    return 1;
                }

                process.WaitForExit();
                int exitCode = process.ExitCode;
                if (exitCode != 0)
                {
                    Console.Error.WriteLine("ERROR: Target exited with code " + exitCode + ".");
                }

                return exitCode;
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR: Failed to launch target batch file: " + ex.Message);
            return 1;
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
