import sys
import os
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from ..core import AppController
from .input import KeyInput

console = Console()

class InteractiveMenu:
    def __init__(self, controller: AppController):
        self.controller = controller
        self.selected_apps = set()
        self.cursor_index = 0
        
        # Initialize selection from enabled apps
        apps = self.controller.list_apps()
        for app in apps:
            if app['enabled']:
                self.selected_apps.add(app['name'])

    def run(self):
        with KeyInput() as key_input:
            while True:
                self._render()
                key = key_input.get_key()
                
                if key == 'q' or key == 'CTRL_C':
                    break
                elif key == 'UP':
                    self.cursor_index = max(0, self.cursor_index - 1)
                elif key == 'DOWN':
                    apps = self.controller.list_apps()
                    self.cursor_index = min(len(apps) - 1, self.cursor_index + 1)
                elif key == 'SPACE':
                    self._toggle_current()
                elif key == 's':
                    self._action_start()
                elif key == 't':
                    self._action_stop()
                elif key == 'r':
                    self._action_restart()
                elif key == 'a':
                    self._select_all()
                elif key == 'x':
                    self._action_stop_all()
                elif key == 'ENTER':
                    # Future: Open context menu for specific app
                    self._toggle_current()

    def _render(self):
        # Clear screen
        print("\033[H\033[J", end="")
        
        apps = self.controller.list_apps()
        
        table = Table(title="Multi-Run Apps Manager", expand=True, box=None)
        table.add_column("", justify="center", width=3) # Cursor column
        table.add_column("Sel", justify="center", width=3)
        table.add_column("App Name", style="bold white")
        table.add_column("Command", style="magenta", max_width=40)
        table.add_column("Status", justify="center")
        table.add_column("Session", style="blue")
        
        for idx, app in enumerate(apps):
            name = app['name']
            
            # Cursor
            cursor = ">" if idx == self.cursor_index else " "
            style = "bold yellow" if idx == self.cursor_index else ""
            
            # Selection
            selected = "[x]" if name in self.selected_apps else "[ ]"
            sel_style = "bold cyan" if name in self.selected_apps else "white"
            
            # Status
            status_info = self.controller.get_app_status(name)
            status_text = status_info.get("status", "UNKNOWN")
            session_name = status_info.get("session", "-")
            
            if status_text == "RUNNING":
                status_display = "[green]● RUNNING[/]"
            elif status_text == "STOPPED":
                status_display = "[dim white]○ STOPPED[/]"
            else:
                status_display = f"[red]{status_text}[/]"

            table.add_row(
                cursor, 
                f"[{sel_style}]{selected}[/]", 
                name, 
                app['command'],
                status_display, 
                session_name,
                style=style
            )
            
        console.print(table)
        
        # Actions Footer
        actions = "[bold green][S] Start[/]  [bold yellow][T] Stop[/]  [bold blue][R] Restart[/]  [bold cyan][Space] Toggle[/]  [bold red][Q] Quit[/]"
        console.print(Panel(actions, title="Actions", border_style="dim"))

    def _toggle_current(self):
        apps = self.controller.list_apps()
        if not apps: return
        
        name = apps[self.cursor_index]['name']
        if name in self.selected_apps:
            self.selected_apps.remove(name)
        else:
            self.selected_apps.add(name)

    def _select_all(self):
        apps = self.controller.list_apps()
        for app in apps:
            self.selected_apps.add(app['name'])

    def _action_start(self):
        self._show_message("Starting selected apps...")
        for app_name in self.selected_apps:
            self.controller.start_app(app_name, wait_for_ready=True)
        time.sleep(1)

    def _action_stop(self):
        self._show_message("Stopping selected apps...")
        for app_name in self.selected_apps:
            self.controller.stop_app(app_name)
        time.sleep(1)

    def _action_restart(self):
        self._show_message("Restarting selected apps...")
        for app_name in self.selected_apps:
            self.controller.stop_app(app_name)
            time.sleep(0.5)
            self.controller.start_app(app_name, wait_for_ready=True)
        time.sleep(1)

    def _action_stop_all(self):
        self._show_message("Stopping ALL apps...")
        self.controller.stop_all()
        time.sleep(1)

    def _show_message(self, msg):
        print(f"\n[INFO] {msg}")
        # In raw mode, we might need to flush or wait
