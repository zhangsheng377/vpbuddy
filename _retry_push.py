#!/usr/bin/env python3
"""Retry git push + retag until GitHub accepts."""
import subprocess, time, sys, os

repo = r"c:\Users\43587\Desktop\codes\vpbuddy"
os.chdir(repo)

max_attempts = 30
for i in range(max_attempts):
    print(f"\n=== Attempt {i+1}/{max_attempts} ===")
    
    # 1. Push main branch
    r = subprocess.run(["git", "push", "origin", "main"],
                       capture_output=True, text=True, timeout=60)
    print(f"push main: rc={r.returncode}")
    if r.stdout: print(r.stdout[-200:])
    if r.stderr: print(r.stderr[-300:])
    
    if r.returncode == 0:
        # 2. Delete remote tag
        r2 = subprocess.run(["git", "push", "--delete", "origin", "v0.8.6"],
                            capture_output=True, text=True, timeout=60)
        print(f"delete tag: rc={r2.returncode}")
        if r2.returncode == 0:
            # 3. Create local tag on current HEAD
            subprocess.run(["git", "tag", "-d", "v0.8.6"],
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["git", "tag", "v0.8.6"],
                           capture_output=True, text=True, timeout=10)
            # 4. Push new tag
            r3 = subprocess.run(["git", "push", "origin", "v0.8.6"],
                                capture_output=True, text=True, timeout=60)
            print(f"push tag: rc={r3.returncode}")
            if r3.returncode == 0:
                print("\n✅ ALL DONE! v0.8.6 tag pushed with fix.")
                sys.exit(0)
            else:
                print(f"tag push failed: {r3.stderr[-200:]}")
        else:
            print(f"delete tag failed (may need to create new tag): {r2.stderr[-200:]}")
            # Try force push tag
            subprocess.run(["git", "tag", "-d", "v0.8.6"],
                           capture_output=True, text=True, timeout=10)
            subprocess.run(["git", "tag", "v0.8.6"],
                           capture_output=True, text=True, timeout=10)
            r3 = subprocess.run(["git", "push", "-f", "origin", "v0.8.6"],
                                capture_output=True, text=True, timeout=60)
            print(f"force push tag: rc={r3.returncode}")
            if r3.returncode == 0:
                print("\n✅ ALL DONE! v0.8.6 tag force-pushed.")
                sys.exit(0)
    
    print(f"  Retrying in 30s...")
    time.sleep(30)

print("Max attempts reached. Still failed.")
sys.exit(1)
