# Kindle Wall Display Setup Guide

Complete guide for setting up your jailbroken Kindle Paperwhite 2 as an automated wall display.

## Prerequisites

✅ Kindle Paperwhite 2 (6th generation)
✅ Firmware 5.12.2.2
✅ Successfully jailbroken (using WatchThis or similar)
✅ Server running and accessible (your display server is already set up)

## Part 1: Download Required Packages

### 1. KUAL and MRPI
- Visit: https://fw.notmarek.com/khf/
- Download latest MRPI tar file (e.g., `kindletool_mrinstaller_20250101.tar`)
- Download KUAL Booklet binary (e.g., `Update_KUALBooklet_hotfix_1.7.N_install.bin`)

### 2. USBNetwork
- Visit: MobileRead Forums → Snapshots thread
- Download: `kindle-usbnetwork-0.57.N-r18979.tar.xz` (or latest)

### 3. Screensaver Hack
- Visit: MobileRead Forums → "K5 FW 5.x ScreenSavers Hack" thread
- Download: `kindle-linkss-0.22.N.tar.xz` (or latest linkss version)
- Download: `kindle-python-0.21.N.tar.xz` (Python for Kindle - dependency)

## Part 2: Install KUAL and MRPI

1. **Connect Kindle to computer** via USB
2. **Extract MRPI tar file** on your computer
3. **Copy to Kindle root**:
   - `extensions/` folder
   - `mrpackages/` folder
4. **Copy KUAL installer**: Place `Update_KUALBooklet_*_install.bin` inside the `mrpackages/` folder
5. **Eject Kindle** safely from computer
6. **On Kindle**:
   - Open search bar (tap magnifying glass)
   - Type: `;log mrpi`
   - Press Enter
7. **Wait** for white screen flash
8. **Verify**: KUAL should appear as a book in your library

## Part 3: Install USBNetwork (for SSH access)

1. **Extract** `kindle-usbnetwork-0.57.N-r18979.tar.xz` on your computer
2. **Find** the `Update_usbnet_*.bin` file for your device
3. **Copy** to Kindle's `mrpackages/` folder
4. **On Kindle**:
   - Open KUAL (the book)
   - Navigate to: Helper → Install MR Packages
   - Wait for installation
5. **Enable USBNetwork**:
   - Open Kindle search bar
   - Type: `;un`
   - Press Enter

## Part 4: Configure WiFi SSH Access

1. **Connect Kindle** to computer via USB
2. **Edit config file**: `/mnt/us/usbnet/etc/config`
3. **Change these lines**:
   ```bash
   # Change from:
   USE_WIFI="false"

   # To:
   USE_WIFI="true"
   ```
4. **Save and eject**
5. **Reboot Kindle**:
   - Open search bar
   - Type: `;un`
   - Press Enter to enable WiFi SSH

## Part 5: Test SSH Connection

From your computer:

```bash
# Find your Kindle's IP address (shown when you type ;un in search)
# Usually something like: 192.168.1.xxx
# 192.168.15.244
# SSH into Kindle (default password: usually blank or "mario")
ssh root@192.168.1.xxx

# If successful, you should see Kindle's shell prompt
```

## Part 6: Install Python and Screensaver Hack

1. **Extract packages** on your computer:
   - `kindle-python-0.21.N.tar.xz`
   - `kindle-linkss-0.22.N.tar.xz`

2. **Copy installer files** to Kindle's `mrpackages/` folder:
   - `Update_python_*_install.bin`
   - `Update_linkss_*_install.bin`

3. **On Kindle**:
   - Open KUAL → Helper → Install MR Packages
   - Install Python first
   - Then install linkss

4. **Verify**: You should now have `/mnt/us/linkss/screensavers/` directory

## Part 7: Upload and Configure Update Script

1. **Edit the script** `kindle_update_display.sh`:
   - Replace `YOUR_SERVER_URL` with your actual server URL
   - Example: `http://your-domain.com:8000/display.png`

2. **Copy script to Kindle** via SSH:
   ```bash
   # From your computer
   scp kindle_update_display.sh root@192.168.1.xxx:/mnt/us/scripts/
   ```

3. **Make script executable**:
   ```bash
   # SSH into Kindle
   ssh root@192.168.1.xxx

   # Make executable
   chmod +x /mnt/us/scripts/update_display.sh
   ```

4. **Test the script**:
   ```bash
   # Run it manually
   /mnt/us/scripts/update_display.sh

   # Check the log
   cat /mnt/us/scripts/update_display.log
   ```

## Part 8: Set Up Automatic Updates (Cron)

### Option A: Using kual-cron

1. **Install kual-cron** via MRPI
2. **Configure cron** via KUAL menu
3. **Add schedule** from `kindle_crontab.txt`

### Option B: Manual crontab (via SSH)

```bash
# SSH into Kindle
ssh root@192.168.1.xxx

# Edit crontab
crontab -e

# Add these lines for updates at 6am, 12pm, 6pm:
0 6 * * * /mnt/us/scripts/update_display.sh
0 12 * * * /mnt/us/scripts/update_display.sh
0 18 * * * /mnt/us/scripts/update_display.sh

# Save and exit
```

## Part 9: Disable OTA Updates (IMPORTANT!)

To prevent Amazon from updating your Kindle and breaking the jailbreak:

### Method 1: Airplane Mode (Simplest)
- Keep Kindle in Airplane Mode
- Only enable WiFi when needed for updates

### Method 2: Block Update Servers

1. **SSH into Kindle**
2. **Edit hosts file**:
   ```bash
   vi /etc/hosts
   ```
3. **Add these lines**:
   ```
   127.0.0.1 todo-ta-g7g.amazon.com
   127.0.0.1 s3.amazonaws.com
   ```
4. **Save and reboot**

## Part 10: Configure Screensaver Settings

1. **Place test image**:
   ```bash
   # Your update script will place images here:
   /mnt/us/linkss/screensavers/display.png
   ```

2. **Configure linkss**:
   - Open KUAL
   - Find linkss settings
   - Enable custom screensavers
   - Set screensaver timeout (recommend: very short or 0)

3. **Test**:
   - Put Kindle to sleep
   - It should show your custom image

## Part 11: Wall Mount Optimization

### Keep Kindle Awake
```bash
# SSH into Kindle
echo 0 > /sys/devices/platform/mxc_epdc_fb/power_off_delay

# To make permanent, add to startup script
```

### Disable Auto-Sleep When Plugged In
```bash
# Edit powerd settings
vi /etc/kdb.src/system/daemon/powerd/suspend_timeout

# Set to very high value or disable
```

## Troubleshooting

### Script not running?
- Check log: `cat /mnt/us/scripts/update_display.log`
- Test manually: `/mnt/us/scripts/update_display.sh`
- Check crontab: `crontab -l`

### No WiFi connection?
- Verify WiFi is enabled (not in Airplane Mode)
- Check `/mnt/us/usbnet/etc/config` has `USE_WIFI="true"`
- Restart USBNetwork: Type `;un` twice (off then on)

### Image not displaying?
- Verify image exists: `ls -lh /mnt/us/linkss/screensavers/`
- Check image dimensions: should be 600x800 or 758x1024
- Restart framework: `/etc/init.d/framework restart`

### Server not accessible?
- Test from Kindle: `wget http://your-server:8000/display.png`
- Check firewall on server
- Verify server is running: `curl http://your-server:8000/display.png`

## Server Configuration

Make sure your display server is accessible:

1. **Check server is running**:
   ```bash
   # On your server
   python -m app.main
   # Or with uv:
   uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

2. **Make accessible from internet** (if needed):
   - Port forward 8000 on your router
   - Or use ngrok/cloudflare tunnel
   - Or deploy to cloud (Heroku, Railway, etc.)

3. **Test accessibility**:
   ```bash
   # From any device on internet
   curl http://your-public-ip:8000/display.png
   ```

## Maintenance

### Daily
- Verify image is updating (check on Kindle)

### Weekly
- Check log file size: `du -h /mnt/us/scripts/update_display.log`
- Review logs for errors: `tail -n 50 /mnt/us/scripts/update_display.log`

### Monthly
- Backup Kindle (just in case)
- Check for script updates
- Verify no OTA updates happened (Settings → Device Info)

## Success Criteria

✅ KUAL installed and accessible
✅ SSH access working via WiFi
✅ Screensaver hack installed
✅ Update script runs without errors
✅ Cron job executing on schedule
✅ Image displays correctly on Kindle screen
✅ OTA updates blocked
✅ Display updates automatically multiple times per day

## Resources

- MobileRead Forums: https://www.mobileread.com/forums/
- Kindle Modding Wiki: https://kindlemodding.org/
- Your server code: `/Users/colcarroll/projects/web-to-kindle-heroku/`
- Update script: `kindle_update_display.sh`
- Cron config: `kindle_crontab.txt`

## Next Steps

Once everything is working:
1. Mount Kindle on wall with power cable
2. Set up systemd service on server to auto-start
3. Monitor logs for first few days
4. Enjoy your automated Kindle display!

---

**Note**: This guide assumes you've already successfully jailbroken your Kindle using WatchThis or similar method. If you encounter issues, consult the MobileRead forums for community support.
