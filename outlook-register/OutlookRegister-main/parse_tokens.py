#!/usr/bin/env python3
"""Parse outlook_token.txt and output email----password----client_id----refresh_token"""
import json
import os
import sys

def parse_token_file(token_path, client_id):
    results = []
    with open(token_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('---')
            if len(parts) < 4:
                continue
            email = parts[0]
            password = parts[1]
            refresh_token = parts[2]
            results.append(f"{email}----{password}----{client_id}----{refresh_token}")
    return results

def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    token_path = os.path.join(project_dir, 'Results', 'outlook_token.txt')
    output_path = os.path.join(project_dir, 'Results', 'formatted_tokens.txt')

    # Get client_id: from config.json first, then from command line
    client_id = ''
    config_path = os.path.join(project_dir, 'config.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        client_id = config.get('oauth2', {}).get('client_id', '')

    # Override with command line argument if provided
    if len(sys.argv) > 1:
        client_id = sys.argv[1]

    if not client_id:
        print("[Error] client_id not set. Set it in config.json or pass as argument:")
        print("  python parse_tokens.py YOUR_CLIENT_ID")
        return

    if not os.path.exists(token_path):
        print(f"[Error] Token file not found: {token_path}")
        return

    results = parse_token_file(token_path, client_id)

    with open(output_path, 'w', encoding='utf-8') as f:
        for line in results:
            f.write(line + '\n')

    print(f"[OK] Parsed {len(results)} entries")
    print(f"[OK] Output: {output_path}")
    print("\n--- Preview (first 3 lines) ---")
    for line in results[:3]:
        print(line)

if __name__ == '__main__':
    main()
