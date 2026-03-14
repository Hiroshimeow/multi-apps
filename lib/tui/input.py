import sys
import tty
import termios
import os

class KeyInput:
    """Handles raw keyboard input for TUI navigation"""
    
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setraw(sys.stdin.fileno())
        return self

    def __exit__(self, type, value, traceback):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)

    def get_key(self):
        """Read a key press and return a simplified code"""
        ch = sys.stdin.read(1)
        
        if ch == '\x1b': # Escape sequence
            seq = sys.stdin.read(2)
            if seq == '[A': return 'UP'
            if seq == '[B': return 'DOWN'
            if seq == '[C': return 'RIGHT'
            if seq == '[D': return 'LEFT'
            return 'ESC'
        elif ch == '\r' or ch == '\n':
            return 'ENTER'
        elif ch == ' ':
            return 'SPACE'
        elif ch == '\x03': # Ctrl+C
            return 'CTRL_C'
        else:
            return ch.lower()
