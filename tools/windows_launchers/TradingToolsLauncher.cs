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
            string? targetBatPath = ResolveTargetBatPath(TargetBat);
            if (targetBatPath is null)
            {
                Console.Error.WriteLine($"ERROR: Could not find required launcher target '{TargetBat}'.");
                Console.Error.WriteLine("Looked relative to the launcher location and current working directory.");
                return 1;
            }

            Console.WriteLine($"Launching {targetBatPath}");
            string cmdArguments = $"/d /c \"\"{targetBatPath}\"{BuildForwardedArgString(args)}\"";
            var startInfo = new ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = cmdArguments,
                WorkingDirectory = Path.GetDirectoryName(targetBatPath) ?? Environment.CurrentDirectory,
                UseShellExecute = false,
            };

            using Process? process = Process.Start(startInfo);
            if (process is null)
            {
                Console.Error.WriteLine("ERROR: Failed to launch cmd.exe for target batch file.");
                return 1;
            }

            process.WaitForExit();
            int exitCode = process.ExitCode;
            if (exitCode != 0)
            {
                Console.Error.WriteLine($"ERROR: Target exited with code {exitCode}.");
            }

            return exitCode;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"ERROR: Failed to launch target batch file: {ex.Message}");
            return 1;
        }
    }

    private static string? ResolveTargetBatPath(string targetBat)
    {
        string baseDirectory = AppContext.BaseDirectory;
        string currentDirectory = Environment.CurrentDirectory;
        var candidates = new List<string>
        {
            Path.Combine(baseDirectory, targetBat),
            Path.Combine(baseDirectory, "..", targetBat),
            Path.Combine(baseDirectory, "..", "..", targetBat),
            Path.Combine(currentDirectory, targetBat),
        };

        foreach (string candidate in candidates.Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }

    private static string BuildForwardedArgString(string[] args)
    {
        if (args.Length == 0)
        {
            return string.Empty;
        }

        return " " + string.Join(" ", args.Select(QuoteForCmd));
    }

    private static string QuoteForCmd(string arg)
    {
        if (arg.Length == 0)
        {
            return "\"\"";
        }

        bool needsQuotes = arg.Any(char.IsWhiteSpace) || arg.Contains('"');
        if (!needsQuotes)
        {
            return arg;
        }

        return "\"" + arg.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
