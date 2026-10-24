// Profilwerk – Startprogramm (C# 5, nur .NET Framework 4, keine Zusatzpakete).
// Startet das Python-Programmfenster ohne Konsole. Gebaut mit build\Build-Exe.ps1.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: AssemblyTitle("Profilwerk")]
[assembly: AssemblyDescription("Startprogramm fuer Profilwerk")]
[assembly: AssemblyCompany("Profilwerk")]
[assembly: AssemblyProduct("Profilwerk")]
[assembly: AssemblyCopyright("Lokales Werkzeug")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

internal static class Startprogramm
{
    private const string AppId = "Profilwerk.Desktop";   // gleicher Name wie in socialscraper\gui.py
    private const string FensterTitel = "Profilwerk";

    [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc proc, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    private delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool dienst = false;
        bool pruefen = false;   // --pruefen: nur prüfen, ob alles da ist (kein Fenster, nichts wird gestartet)
        foreach (string a in args)
        {
            if (string.Equals(a, "--dienst", StringComparison.OrdinalIgnoreCase)) dienst = true;
            if (string.Equals(a, "--pruefen", StringComparison.OrdinalIgnoreCase)) pruefen = true;
        }
        try
        {
            string ordner = FindeProgrammordner();
            if (ordner == null)
            {
                if (dienst || pruefen) return 3; // Autostart/Prüfung: keine Fenster, wenn nichts da ist
                ordner = FrageNachOrdner();
                if (ordner == null) return 3;
            }
            string pythonw = Path.Combine(ordner, @".venv\Scripts\pythonw.exe");
            if (pruefen) return File.Exists(pythonw) ? 0 : 2;
            if (!File.Exists(pythonw))
            {
                if (dienst) return 2;
                if (Frage("Profilwerk kann noch nicht starten",
                          "Die Programmumgebung fehlt noch (Ordner \".venv\").\r\n\r\nMit \"Einrichten starten\" wird sie angelegt. Das dauert ein paar Minuten und braucht Internet.",
                          "Einrichten starten", "Schließen"))
                {
                    StarteEinrichten(ordner);
                }
                return 2;
            }
            if (dienst)
            {
                Starte(pythonw, "-m socialscraper daemon", ordner);
                return 0;
            }
            if (LaeuftSchon())
            {
                if (!HoleFensterNachVorn())
                {
                    Info("Profilwerk läuft schon",
                         "Profilwerk ist bereits geöffnet oder startet gerade. Bitte einen Moment warten und in der Taskleiste nachsehen.");
                }
                return 0;
            }
            Starte(pythonw, "-m socialscraper.gui", ordner);
            return 0;
        }
        catch (Exception ex)
        {
            if (!dienst) Info("Profilwerk konnte nicht gestartet werden", ex.Message);
            return 1;
        }
    }

    // ---- Programmordner ----

    private static bool IstProgrammordner(string pfad)
    {
        if (string.IsNullOrEmpty(pfad)) return false;
        try { return File.Exists(Path.Combine(pfad, @"socialscraper\gui.py")); }
        catch (Exception) { return false; }
    }

    private static string OrtDatei()
    {
        string lokal = Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if (string.IsNullOrEmpty(lokal)) return null;
        return Path.Combine(Path.Combine(lokal, "Profilwerk"), "ort.txt");
    }

    private static string FindeProgrammordner()
    {
        string neben = AppDomain.CurrentDomain.BaseDirectory;
        if (IstProgrammordner(neben)) return neben.TrimEnd('\\');
        string datei = OrtDatei();
        if (datei != null && File.Exists(datei))
        {
            try
            {
                foreach (string zeile in File.ReadAllLines(datei, Encoding.UTF8))
                {
                    string pfad = zeile.Trim().Trim('"');
                    if (IstProgrammordner(pfad)) return pfad.TrimEnd('\\');
                }
            }
            catch (Exception) { }
        }
        return null;
    }

    private static string FrageNachOrdner()
    {
        if (!Frage("Programmordner nicht gefunden",
                   "Profilwerk findet seinen Programmordner nicht.\r\n\r\nWähle den Ordner, in dem Einrichten.cmd und der Ordner \"socialscraper\" liegen.",
                   "Ordner wählen", "Schließen"))
            return null;
        using (FolderBrowserDialog d = new FolderBrowserDialog())
        {
            d.Description = "Ordner mit Einrichten.cmd und dem Ordner \"socialscraper\" wählen";
            if (d.ShowDialog() != DialogResult.OK) return null;
            if (!IstProgrammordner(d.SelectedPath))
            {
                Info("Falscher Ordner", "In diesem Ordner liegt kein Profilwerk-Programm.");
                return null;
            }
            try
            {
                string datei = OrtDatei();
                if (datei != null)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(datei));
                    File.WriteAllText(datei, d.SelectedPath + "\r\n", new UTF8Encoding(false));
                }
            }
            catch (Exception) { }
            return d.SelectedPath.TrimEnd('\\');
        }
    }

    // ---- Starten ----

    private static void Starte(string exe, string argumente, string arbeitsordner)
    {
        ProcessStartInfo psi = new ProcessStartInfo(exe, argumente);
        psi.WorkingDirectory = arbeitsordner;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        Process.Start(psi);   // wartet nicht
    }

    private static void StarteEinrichten(string ordner)
    {
        string cmd = Path.Combine(ordner, "Einrichten.cmd");
        if (!File.Exists(cmd))
        {
            Info("Einrichten.cmd fehlt", "Die Datei Einrichten.cmd liegt nicht im Programmordner:\r\n" + ordner);
            return;
        }
        ProcessStartInfo psi = new ProcessStartInfo(cmd);
        psi.WorkingDirectory = ordner;
        psi.UseShellExecute = true;
        Process.Start(psi);
    }

    // ---- Zweiter Start ----

    private static bool LaeuftSchon()
    {
        try
        {
            Mutex m = Mutex.OpenExisting("Local\\" + AppId);
            m.Close();
            return true;
        }
        catch (WaitHandleCannotBeOpenedException) { return false; }
        catch (UnauthorizedAccessException) { return true; }
        catch (Exception) { return false; }
    }

    private static bool HoleFensterNachVorn()
    {
        IntPtr gefunden = IntPtr.Zero;
        EnumWindows(delegate (IntPtr h, IntPtr l)
        {
            if (!IsWindowVisible(h)) return true;
            StringBuilder sb = new StringBuilder(256);
            GetWindowText(h, sb, 256);
            if (sb.ToString() != FensterTitel) return true;
            uint pid;
            GetWindowThreadProcessId(h, out pid);
            try
            {
                string name = Process.GetProcessById((int)pid).ProcessName.ToLowerInvariant();
                if (!name.StartsWith("python")) return true;   // z. B. ein Explorer-Ordner „Profilwerk“
            }
            catch (Exception) { return true; }
            gefunden = h;
            return false;
        }, IntPtr.Zero);
        if (gefunden == IntPtr.Zero) return false;
        if (IsIconic(gefunden)) ShowWindow(gefunden, 9); // SW_RESTORE
        SetForegroundWindow(gefunden);
        return true;
    }

    // ---- Meldungen ----

    private static void Info(string titel, string text)
    {
        MessageBox.Show(text, titel, MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    // true = erster Knopf
    private static bool Frage(string titel, string text, string ja, string nein)
    {
        using (Form f = new Form())
        {
            f.Text = titel;
            f.StartPosition = FormStartPosition.CenterScreen;
            f.FormBorderStyle = FormBorderStyle.FixedDialog;
            f.MaximizeBox = false;
            f.MinimizeBox = false;
            f.ShowInTaskbar = true;
            f.Font = new Font("Segoe UI", 9.5f);
            f.ClientSize = new Size(460, 170);
            Label l = new Label();
            l.Text = text;
            l.SetBounds(16, 16, 428, 100);
            f.Controls.Add(l);
            Button b1 = new Button();
            b1.Text = ja;
            b1.DialogResult = DialogResult.OK;
            b1.SetBounds(f.ClientSize.Width - 16 - 120 - 8 - 150, 126, 150, 30);
            Button b2 = new Button();
            b2.Text = nein;
            b2.DialogResult = DialogResult.Cancel;
            b2.SetBounds(f.ClientSize.Width - 16 - 120, 126, 120, 30);
            f.Controls.Add(b1);
            f.Controls.Add(b2);
            f.AcceptButton = b1;
            f.CancelButton = b2;
            return f.ShowDialog() == DialogResult.OK;
        }
    }
}
