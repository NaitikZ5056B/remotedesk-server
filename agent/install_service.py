"""
RemoteDesk Agent - Service Installer
Installs the agent as a system service that auto-starts on boot.
Supports Windows (NSSM/Task Scheduler), macOS (launchd), and Linux (systemd).
"""
import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path


AGENT_DIR = Path(__file__).parent.resolve()
PYTHON_PATH = sys.executable
MAIN_SCRIPT = AGENT_DIR / "main.py"
SERVICE_NAME = "RemoteDeskAgent"


def install_windows():
    """Install as a Windows service using Task Scheduler (no admin required for current user)."""
    print("[Windows] Installing RemoteDesk Agent...")

    # Method 1: Task Scheduler (works without admin for current user login)
    task_xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>RemoteDesk Agent - Remote desktop control service</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{PYTHON_PATH}</Command>
      <Arguments>{MAIN_SCRIPT}</Arguments>
      <WorkingDirectory>{AGENT_DIR}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""

    xml_path = AGENT_DIR / "remotedesk_task.xml"
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(task_xml)

    result = subprocess.run(
        ["schtasks", "/Create", "/TN", SERVICE_NAME, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True
    )

    if result.returncode == 0:
        print(f"✅ Task '{SERVICE_NAME}' created successfully!")
        print("   It will auto-start on login and restart on failure.")
        print(f"\n   Start now:  schtasks /Run /TN {SERVICE_NAME}")
        print(f"   Stop:       schtasks /End /TN {SERVICE_NAME}")
        print(f"   Uninstall:  schtasks /Delete /TN {SERVICE_NAME} /F")
    else:
        print(f"❌ Failed: {result.stderr}")
        print("   Try running as Administrator.")

    # Cleanup XML
    xml_path.unlink(missing_ok=True)


def install_macos():
    """Install as a macOS launchd agent."""
    print("[macOS] Installing RemoteDesk Agent...")

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.remotedesk.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON_PATH}</string>
        <string>{MAIN_SCRIPT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{AGENT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{AGENT_DIR}/logs/agent_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{AGENT_DIR}/logs/agent_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>"""

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.remotedesk.agent.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    with open(plist_path, "w") as f:
        f.write(plist_content)

    # Load the agent
    subprocess.run(["launchctl", "load", str(plist_path)])

    print(f"✅ LaunchAgent installed at {plist_path}")
    print("   It will auto-start on login.")
    print(f"\n   Start now:  launchctl start com.remotedesk.agent")
    print(f"   Stop:       launchctl stop com.remotedesk.agent")
    print(f"   Uninstall:  launchctl unload {plist_path} && rm {plist_path}")


def install_linux():
    """Install as a systemd user service."""
    print("[Linux] Installing RemoteDesk Agent...")

    service_content = f"""[Unit]
Description=RemoteDesk Agent - Remote desktop control service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={PYTHON_PATH} {MAIN_SCRIPT}
WorkingDirectory={AGENT_DIR}
Restart=always
RestartSec=5
Environment=DISPLAY=:0
Environment=XAUTHORITY=%h/.Xauthority

StandardOutput=append:{AGENT_DIR}/logs/agent_stdout.log
StandardError=append:{AGENT_DIR}/logs/agent_stderr.log

[Install]
WantedBy=default.target
"""

    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "remotedesk-agent.service"

    with open(service_path, "w") as f:
        f.write(service_content)

    # Reload and enable
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "remotedesk-agent"])
    subprocess.run(["systemctl", "--user", "start", "remotedesk-agent"])

    # Enable lingering so service runs even when user is not logged in
    subprocess.run(["loginctl", "enable-linger", os.getlogin()])

    print(f"✅ Systemd user service installed at {service_path}")
    print("   It will auto-start on boot (with lingering enabled).")
    print(f"\n   Status:     systemctl --user status remotedesk-agent")
    print(f"   Stop:       systemctl --user stop remotedesk-agent")
    print(f"   Logs:       journalctl --user -u remotedesk-agent -f")
    print(f"   Uninstall:  systemctl --user disable remotedesk-agent")


def uninstall():
    """Uninstall the service."""
    system = platform.system()
    if system == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", SERVICE_NAME, "/F"])
    elif system == "Darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / "com.remotedesk.agent.plist"
        subprocess.run(["launchctl", "unload", str(plist_path)])
        plist_path.unlink(missing_ok=True)
    elif system == "Linux":
        subprocess.run(["systemctl", "--user", "stop", "remotedesk-agent"])
        subprocess.run(["systemctl", "--user", "disable", "remotedesk-agent"])
        service_path = Path.home() / ".config" / "systemd" / "user" / "remotedesk-agent.service"
        service_path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"])

    print(f"✅ RemoteDesk Agent service uninstalled.")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "install"

    if action == "install":
        system = platform.system()
        if system == "Windows":
            install_windows()
        elif system == "Darwin":
            install_macos()
        elif system == "Linux":
            install_linux()
        else:
            print(f"Unsupported OS: {system}")
    elif action == "uninstall":
        uninstall()
    else:
        print(f"Usage: python {sys.argv[0]} [install|uninstall]")
