param(
    [ValidateSet('screen', 'palette')][string]$Mode = 'screen',
    [ValidatePattern('^#[0-9a-fA-F]{6}$')][string]$InitialColor = '#000000'
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

// Consume a selecting click and its release, without clicking another app.
public sealed class MusicColorSelection {
    public bool Complete;
    public string Color;
    private int release;
    public bool Handle(int message, Func<uint> sample) {
        if (release != 0) {
            if (message == release) { Complete = true; return true; }
            return message == 0x201 || message == 0x204;
        }
        if (message != 0x201 && message != 0x204) return false;
        if (message == 0x201) {
            uint pixel = sample();
            if (pixel == 0xffffffff) throw new InvalidOperationException("Pixel unavailable");
            Color = String.Format("#{0:x2}{1:x2}{2:x2}", pixel & 255, (pixel >> 8) & 255, (pixel >> 16) & 255);
        }
        release = message == 0x201 ? 0x202 : 0x205;
        return true;
    }
}

public sealed class MusicScreenColor : Form {
    [StructLayout(LayoutKind.Sequential)] private struct Point { public int X, Y; }
    private delegate IntPtr MouseProc(int code, IntPtr message, IntPtr data);
    [DllImport("user32.dll")] private static extern IntPtr SetWindowsHookEx(int id, MouseProc callback, IntPtr module, uint thread);
    [DllImport("user32.dll")] private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")] private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr message, IntPtr data);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] private static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll")] private static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] private static extern IntPtr GetDC(IntPtr window);
    [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr window, IntPtr dc);
    [DllImport("gdi32.dll")] private static extern uint GetPixel(IntPtr dc, int x, int y);
    [DllImport("user32.dll")] private static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    private readonly MusicColorSelection selection = new MusicColorSelection();
    private readonly Timer timer = new Timer();
    private MouseProc callback;
    private IntPtr hook;
    private readonly DateTime deadline = DateTime.UtcNow.AddSeconds(60);
    private string failure;
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { CreateParams p = base.CreateParams; p.ExStyle |= 0x08000000; return p; }
    }
    public MusicScreenColor() {
        FormBorderStyle = FormBorderStyle.FixedToolWindow;
        Text = "Astra Music";
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(360, 58);
        Rectangle area = Screen.FromPoint(Cursor.Position).WorkingArea;
        Location = new System.Drawing.Point(area.Left + 16, area.Top + 16);
        Label hint = new Label();
        hint.Dock = DockStyle.Fill;
        hint.TextAlign = ContentAlignment.MiddleCenter;
        hint.Text = "Выберите точку на экране. Esc / правая кнопка — отмена.";
        Controls.Add(hint);
        timer.Interval = 40;
        timer.Tick += delegate {
            if (selection.Complete || DateTime.UtcNow >= deadline || GetAsyncKeyState(27) < 0) Close();
        };
    }
    private static uint ReadPixel() {
        Point point;
        if (!GetCursorPos(out point)) throw new InvalidOperationException("Cursor unavailable");
        IntPtr dc = GetDC(IntPtr.Zero);
        if (dc == IntPtr.Zero) throw new InvalidOperationException("Desktop unavailable");
        try { return GetPixel(dc, point.X, point.Y); }
        finally { ReleaseDC(IntPtr.Zero, dc); }
    }
    protected override void OnShown(EventArgs e) {
        base.OnShown(e);
        callback = delegate(int code, IntPtr message, IntPtr data) {
            if (code >= 0) {
                try {
                    if (selection.Handle(message.ToInt32(), ReadPixel)) return new IntPtr(1);
                } catch { failure = "pixel"; BeginInvoke(new Action(Close)); return new IntPtr(1); }
            }
            return CallNextHookEx(hook, code, message, data);
        };
        hook = SetWindowsHookEx(14, callback, GetModuleHandle(null), 0);
        if (hook == IntPtr.Zero) { failure = "hook"; Close(); return; }
        timer.Start();
    }
    protected override void Dispose(bool disposing) {
        if (hook != IntPtr.Zero) { UnhookWindowsHookEx(hook); hook = IntPtr.Zero; }
        if (disposing) timer.Dispose();
        base.Dispose(disposing);
        GC.KeepAlive(callback);
    }
    public static string RunPicker() {
        try { SetThreadDpiAwarenessContext(new IntPtr(-4)); } catch (EntryPointNotFoundException) { }
        using (MusicScreenColor form = new MusicScreenColor()) {
            Application.Run(form);
            if (form.failure != null) return "{\"error\":\"picker unavailable\"}";
            return form.selection.Complete && form.selection.Color != null
                ? "{\"color\":\"" + form.selection.Color + "\"}" : "{\"cancelled\":true}";
        }
    }
    public static string RunPalette(string initial) {
        // A real topmost owner keeps the Windows palette above Astra.
        using (Form owner = new Form())
        using (ColorDialog palette = new ColorDialog()) {
            owner.FormBorderStyle = FormBorderStyle.None;
            owner.ShowInTaskbar = false;
            owner.TopMost = true;
            owner.Opacity = 0;
            owner.StartPosition = FormStartPosition.CenterScreen;
            owner.ClientSize = new Size(1, 1);
            palette.FullOpen = true;
            palette.Color = ColorTranslator.FromHtml(initial);
            owner.Show();
            if (palette.ShowDialog(owner) != DialogResult.OK) return "{\"cancelled\":true}";
            Color color = palette.Color;
            return String.Format("{{\"color\":\"#{0:x2}{1:x2}{2:x2}\"}}", color.R, color.G, color.B);
        }
    }
}
'@
if ($Mode -eq 'palette') { [MusicScreenColor]::RunPalette($InitialColor) }
else { [MusicScreenColor]::RunPicker() }
