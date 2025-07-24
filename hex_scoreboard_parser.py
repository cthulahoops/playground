#!/usr/bin/env python3
"""
Parse kitty window or tmux pane content to extract dailyhex game scoreboard with player details.
Auto-detects and prioritizes kitty, falls back to tmux if needed.
Usage: python hex_scoreboard_parser.py [<session:window.pane>]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Dict, Optional


def find_dailyhex_kitty_window() -> Optional[int]:
    """Find kitty window containing dailyhex content."""
    kitty_socket = os.environ.get("KITTY_LISTEN_ON")
    if not kitty_socket:
        return None

    try:
        # Get list of all windows
        result = subprocess.run(
            ["kitty", "@", "--to", kitty_socket, "ls"],
            capture_output=True,
            text=True,
            check=True,
        )

        import json as json_mod

        windows_data = json_mod.loads(result.stdout)

        # Search through all windows for one with SSH to hex
        for os_window in windows_data:
            for tab in os_window.get("tabs", []):
                for window in tab.get("windows", []):
                    window_id = window["id"]

                    # Check if this window has an SSH connection to hex
                    for process in window.get("foreground_processes", []):
                        cmdline = process.get("cmdline", [])
                        if (
                            len(cmdline) >= 2
                            and cmdline[0] == "ssh"
                            and cmdline[1] == "hex"
                        ):
                            return window_id

        return None
    except (subprocess.CalledProcessError, json_mod.JSONDecodeError):
        return None


def capture_kitty_window(window_id: int) -> str:
    """Capture kitty window content with ANSI escape sequences."""
    kitty_socket = os.environ.get("KITTY_LISTEN_ON")
    if not kitty_socket:
        raise RuntimeError("KITTY_LISTEN_ON not set")

    try:
        result = subprocess.run(
            [
                "kitty",
                "@",
                "--to",
                kitty_socket,
                "get-text",
                "--ansi",
                "--match",
                f"id:{window_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error capturing kitty window: {e}", file=sys.stderr)
        sys.exit(1)


def capture_tmux_pane(pane_target: str) -> str:
    """Capture tmux pane content with ANSI escape sequences."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane_target, "-e", "-p"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error capturing tmux pane: {e}", file=sys.stderr)
        sys.exit(1)


def find_dailyhex_tmux_pane() -> Optional[str]:
    """Find tmux pane containing dailyhex content."""
    try:
        # Get list of all panes with their commands
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_name}:#{window_index}.#{pane_index} #{pane_current_command}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # Look for panes running ssh
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(" ", 1)
            if len(parts) >= 2:
                pane_target = parts[0]
                command = parts[1]

                if command == "ssh":
                    # Test if this pane contains dailyhex content
                    try:
                        test_result = subprocess.run(
                            ["tmux", "capture-pane", "-t", pane_target, "-e", "-p"],
                            capture_output=True,
                            text=True,
                            check=True,
                        )

                        if "dailyhex!" in test_result.stdout:
                            return pane_target
                    except subprocess.CalledProcessError:
                        continue

        return None
    except subprocess.CalledProcessError:
        return None


def capture_content(target: Optional[str] = None) -> str:
    """Capture content from kitty or tmux, auto-detecting the best source."""
    # First try kitty if available
    if os.environ.get("KITTY_LISTEN_ON"):
        window_id = find_dailyhex_kitty_window()
        if window_id:
            return capture_kitty_window(window_id)

    # Try tmux auto-detection if we're in tmux
    if os.environ.get("TMUX"):
        pane_target = find_dailyhex_tmux_pane()
        if pane_target:
            return capture_tmux_pane(pane_target)

    # Fall back to tmux with explicit target
    if target:
        return capture_tmux_pane(target)

    print("Error: Could not find dailyhex content in kitty or tmux", file=sys.stderr)
    sys.exit(1)


def parse_ansi_colors(text: str, background_only: bool = True) -> List[str]:
    """Extract hex colors from ANSI escape sequences."""
    if background_only:
        # Pattern for RGB background color codes: supports both ; and : separators
        # \x1b[48;2;R;G;B m (tmux) and \x1b[48:2:R:G:B m (kitty)
        pattern = r"\x1b\[48[;:]2[;:](\d+)[;:](\d+)[;:](\d+)m"
    else:
        # Pattern for RGB foreground color codes: supports both ; and : separators
        # \x1b[38;2;R;G;B m (tmux) and \x1b[38:2:R:G:B m (kitty)
        pattern = r"\x1b\[(?:\d+;)*38[;:]2[;:](\d+)[;:](\d+)[;:](\d+)m"

    matches = re.findall(pattern, text)

    hex_colors = []
    for r, g, b in matches:
        hex_color = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
        hex_colors.append(hex_color)

    return hex_colors


def parse_scoreboard(content: str) -> Dict:
    """Parse the scoreboard content to extract player data."""
    lines = content.split("\n")

    # Find the day number and solution from dailyhex title color
    day_number = None
    solution = None
    for line in lines:
        if "day" in line and ("·" in line or ">" in line):
            day_match = re.search(r"day (\d+)", line)
            if day_match:
                day_number = day_match.group(1)

        # Extract solution from dailyhex title color (foreground color)
        if "dailyhex!" in line:
            colors = parse_ansi_colors(line, background_only=False)
            if colors:
                solution = colors[0]  # The title color is the solution

    # Find player data lines (lines with names and moves)
    players = {}
    for line in lines:
        # Skip header lines and empty lines
        if (
            not line.strip()
            or "name" in line
            or "moves" in line
            or "dailyhex" in line
            or "day" in line
        ):
            continue

        # Look for lines with player names and move counts
        # Pattern: player_name ... colored_blocks ... number
        # Use a more flexible pattern to handle ANSI sequences
        clean_line = re.sub(
            r"\x1b\[[0-9;]*m", "", line
        )  # Remove ANSI codes for matching
        match = re.search(r"^\s*(\w+)\s+.*?(\d+)\s*$", clean_line)
        if match:
            player_name = match.group(1)
            moves = int(match.group(2))

            # Extract colors from this line
            colors = parse_ansi_colors(line)

            players[player_name] = {"moves": moves, "guesses": colors}

    return {
        "title": "dailyhex!",
        "day": day_number,
        "solution": solution,
        "players": players,
    }


def hex_to_ansi_bg(hex_color: str) -> str:
    """Convert hex color to ANSI background color escape sequence."""
    # Remove # if present
    hex_color = hex_color.lstrip("#")

    # Convert hex to RGB
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Return ANSI escape sequence for RGB background color
    return f"\x1b[48;2;{r};{g};{b}m"


def evaluate_guess(
    guess: str, solution: str, impossible_digits: set = None
) -> List[str]:
    """
    Evaluate a guess against the solution using Wordle rules.
    Returns a list of colored characters for each digit in the guess.
    Green = correct digit in correct position
    Yellow = correct digit in wrong position
    Red = impossible digit (known to not be in solution)
    Default = wrong digit
    """
    # Remove # if present and make uppercase
    guess = guess.lstrip("#").upper()
    solution = solution.lstrip("#").upper()

    if impossible_digits is None:
        impossible_digits = set()

    if len(guess) != 6 or len(solution) != 6:
        return list(guess)  # Return uncolored if invalid

    result = []
    solution_chars = list(solution)
    guess_chars = list(guess)

    # First pass: mark impossible digits (red)
    for i in range(6):
        if guess_chars[i] in impossible_digits:
            result.append(f"\x1b[31m{guess_chars[i]}\x1b[0m")  # Red
            guess_chars[i] = None  # Mark as processed
        else:
            result.append(None)  # Placeholder

    # Second pass: mark correct positions (green)
    for i in range(6):
        if guess_chars[i] is not None and guess_chars[i] == solution_chars[i]:
            result[i] = f"\x1b[32m{guess_chars[i]}\x1b[0m"  # Green
            solution_chars[i] = None  # Mark as used
            guess_chars[i] = None  # Mark as processed

    # Third pass: mark wrong positions (yellow)
    for i in range(6):
        if guess_chars[i] is not None:  # Not already processed
            char = guess_chars[i]
            if char in solution_chars:
                # Find first occurrence and mark as used
                idx = solution_chars.index(char)
                solution_chars[idx] = None
                result[i] = f"\x1b[33m{char}\x1b[0m"  # Yellow
            else:
                result[i] = char  # Default color

    return result


def get_impossible_digits(previous_guesses: List[str], solution: str) -> set:
    """
    Determine which digits are impossible based on previous guesses.
    A digit is impossible if it appeared in a previous guess but got no feedback (not green or yellow).
    """
    impossible_digits = set()

    for guess in previous_guesses:
        guess_clean = guess.lstrip("#").upper()
        solution_clean = solution.lstrip("#").upper()

        if len(guess_clean) != 6 or len(solution_clean) != 6:
            continue

        # Track which digits in the guess got feedback
        solution_chars = list(solution_clean)
        guess_chars = list(guess_clean)

        # Mark correct positions
        for i in range(6):
            if guess_chars[i] == solution_chars[i]:
                solution_chars[i] = None  # Mark as used
                guess_chars[i] = None  # Mark as processed

        # Mark wrong positions (yellow)
        for i in range(6):
            if guess_chars[i] is not None:
                char = guess_chars[i]
                if char in solution_chars:
                    idx = solution_chars.index(char)
                    solution_chars[idx] = None
                    guess_chars[i] = None  # Mark as processed

        # Any remaining unprocessed digits are impossible
        for char in guess_chars:
            if char is not None:
                impossible_digits.add(char)

    return impossible_digits


def format_colored_hex(guess: str, solution: str, impossible_digits: set = None) -> str:
    """Format a hex color with Wordle-style evaluation."""
    colored_chars = evaluate_guess(guess, solution, impossible_digits)
    return "#" + "".join(colored_chars)


def find_solution(data: Dict) -> str:
    """Get the solution from the parsed data."""
    return data.get("solution")


def format_output(data: Dict) -> str:
    """Format the parsed data into a readable output."""
    output = []
    output.append(f"=== {data['title']} ===")
    if data["day"]:
        output.append(f"Day: {data['day']}")

    solution = find_solution(data)
    if solution:
        output.append(f"Solution: {solution}")
    output.append("")

    for player_name, player_data in data["players"].items():
        output.append(f"{player_name} ({player_data['moves']} moves):")

        # Track impossible digits for this player
        for i, color in enumerate(player_data["guesses"], 1):
            # Two spaces with background color + reset
            color_block = f"{hex_to_ansi_bg(color)}  \x1b[0m"

            if solution:
                # Get impossible digits from previous guesses
                previous_guesses = player_data["guesses"][: i - 1]
                impossible_digits = get_impossible_digits(previous_guesses, solution)
                colored_hex = format_colored_hex(color, solution, impossible_digits)
            else:
                colored_hex = color

            output.append(f"  {i}. {color_block} {colored_hex}")
        output.append("")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Parse terminal content to extract dailyhex game scoreboard (auto-detects kitty or tmux)"
    )
    parser.add_argument(
        "pane_target",
        nargs="?",
        help="Tmux pane target (e.g., main:1.1) - only needed if not using kitty or auto-detection fails",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format (without Wordle evaluations)",
    )

    args = parser.parse_args()

    content = capture_content(args.pane_target)
    data = parse_scoreboard(content)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        output = format_output(data)
        print(output)


if __name__ == "__main__":
    main()
