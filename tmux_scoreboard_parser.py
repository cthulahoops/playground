#!/usr/bin/env python3
"""
Parse tmux pane content to extract dailyhex game scoreboard with player details.
Usage: python tmux_scoreboard_parser.py <session:window.pane>
"""

import re
import subprocess
import sys
from typing import List, Dict, Tuple


def capture_tmux_pane(pane_target: str) -> str:
    """Capture tmux pane content with ANSI escape sequences."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane_target, "-e", "-p"],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error capturing tmux pane: {e}", file=sys.stderr)
        sys.exit(1)


def parse_ansi_colors(text: str) -> List[str]:
    """Extract hex colors from ANSI escape sequences."""
    # Pattern for RGB color codes: \x1b[48;2;R;G;B m
    pattern = r'\x1b\[48;2;(\d+);(\d+);(\d+)m'
    matches = re.findall(pattern, text)
    
    hex_colors = []
    for r, g, b in matches:
        hex_color = f"#{int(r):02X}{int(g):02X}{int(b):02X}"
        hex_colors.append(hex_color)
    
    return hex_colors


def parse_scoreboard(content: str) -> Dict:
    """Parse the scoreboard content to extract player data."""
    lines = content.split('\n')
    
    # Find the title and day
    title_line = None
    day_number = None
    for line in lines:
        if 'dailyhex!' in line:
            title_line = line
        if 'day' in line and '·' in line:
            day_match = re.search(r'day (\d+)', line)
            if day_match:
                day_number = day_match.group(1)
    
    # Find player data lines (lines with names and moves)
    players = {}
    for line in lines:
        # Skip header lines and empty lines
        if not line.strip() or 'name' in line or 'moves' in line or 'dailyhex' in line or 'day' in line:
            continue
            
        # Look for lines with player names and move counts
        # Pattern: player_name ... colored_blocks ... number
        # Use a more flexible pattern to handle ANSI sequences
        clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line)  # Remove ANSI codes for matching
        match = re.search(r'^\s*(\w+)\s+.*?(\d+)\s*$', clean_line)
        if match:
            player_name = match.group(1)
            moves = int(match.group(2))
            
            # Extract colors from this line
            colors = parse_ansi_colors(line)
            
            players[player_name] = {
                'moves': moves,
                'guesses': colors
            }
    
    return {
        'title': 'dailyhex!',
        'day': day_number,
        'players': players
    }


def hex_to_ansi_bg(hex_color: str) -> str:
    """Convert hex color to ANSI background color escape sequence."""
    # Remove # if present
    hex_color = hex_color.lstrip('#')
    
    # Convert hex to RGB
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    
    # Return ANSI escape sequence for RGB background color
    return f"\x1b[48;2;{r};{g};{b}m"


def format_output(data: Dict) -> str:
    """Format the parsed data into a readable output."""
    output = []
    output.append(f"=== {data['title']} ===")
    if data['day']:
        output.append(f"Day: {data['day']}")
    output.append("")
    
    # Sort players by number of moves (ascending)
    sorted_players = sorted(data['players'].items(), key=lambda x: x[1]['moves'])
    
    for player_name, player_data in sorted_players:
        output.append(f"{player_name} ({player_data['moves']} moves):")
        for i, color in enumerate(player_data['guesses'], 1):
            # Create colored block with ANSI escape sequences
            color_block = f"{hex_to_ansi_bg(color)}  \x1b[0m"  # Two spaces with background color + reset
            output.append(f"  {i}. {color_block} {color}")
        output.append("")
    
    return '\n'.join(output)


def main():
    if len(sys.argv) != 2:
        print("Usage: python tmux_scoreboard_parser.py <session:window.pane>")
        print("Example: python tmux_scoreboard_parser.py main:1.1")
        sys.exit(1)
    
    pane_target = sys.argv[1]
    
    # Capture tmux pane content
    content = capture_tmux_pane(pane_target)
    
    # Parse the scoreboard
    data = parse_scoreboard(content)
    
    # Format and print output
    output = format_output(data)
    print(output)


if __name__ == "__main__":
    main()