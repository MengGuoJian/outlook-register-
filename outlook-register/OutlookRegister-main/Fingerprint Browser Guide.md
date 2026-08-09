# OutlookRegister - Fingerprint Browser Guide

## Quick Start

### Step 1: Start fingerprint browser
Double-click start_fingerprint.bat. This launches chrome.exe with fingerprint=1000 and proxy=127.0.0.1:7897.
The browser window opens and stays open.

### Step 2: Run registration
Keep the browser window open. Double-click run.bat.
The script will use launch_persistent_context to connect to the same profile.

## Config (config.json)
{
    choose_browser: playwright,
    playwright: {
        browser_path: C:////Users////meng////AppData////Local////Chromium////Application////chrome.exe,
        browser_args: [
            --fingerprint=1000,
            --user-data-dir=D:////profiles////outlook_reg,
            --lang=zh-CN
        ]
    }
}

## Notes
- DO NOT pass proxy in config.json when using fingerprint browser (browser handles it)
- The --user-data-dir arg is filtered out in code and passed as parameter instead
- To change fingerprint: edit FINGERPRINT in start_fingerprint.bat AND browser_args in config.json
- Start a fresh profile by deleting D://profiles//outlook_reg
