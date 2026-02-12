---
name: "video-downloader"
description: "Downloads videos from various sites using yt-dlp. Invoke when user provides a video link and requests to download it."
---

# Video Downloader

This skill helps you download videos from thousands of websites using yt-dlp, a powerful command-line downloader.

## Usage

1. Provide a video link (e.g., YouTube, Vimeo, etc.)
2. The skill will automatically use yt-dlp to download the video
3. The downloaded video will be saved to the current directory

## Supported Sites

yt-dlp supports thousands of sites including:
- YouTube
- Vimeo
- Dailymotion
- Facebook
- Twitter
- Instagram
- Bilibili
- And many more

## Requirements

- yt-dlp must be installed on the system
- For installation instructions, visit: https://github.com/yt-dlp/yt-dlp#installation
- For merging video and audio files, ffmpeg is recommended

## Example

**User Input:**
```
https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

**Skill Action:**
- Downloads the video using yt-dlp
- Saves it to the current directory

## Advanced Options

You can specify additional yt-dlp options if needed, such as:
- Format selection
- Output directory
- Subtitle options
- And more

Refer to the yt-dlp documentation for all available options: https://github.com/yt-dlp/yt-dlp#usage-and-options

## Common Issues and Solutions

### YouTube 403 Forbidden Error

**Causes:**
- YouTube's anti-scraping mechanisms detecting the download tool
- IP address restrictions or temporary bans
- Video access permissions

**Solutions:**
1. **Use browser cookies:**
   ```bash
   yt-dlp --cookies-from-browser chrome "VIDEO_URL"
   ```

2. **Adjust network settings:**
   ```bash
   HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= yt-dlp --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36" "VIDEO_URL"
   ```

3. **Add download delay:**
   ```bash
   yt-dlp --sleep-interval 10 --max-sleep-interval 30 "VIDEO_URL"
   ```

**Alternative Approaches:**
- Use browser extensions like Video DownloadHelper
- Try different network connections or VPNs
- Wait for a period of time before retrying
- Check if the video has any access restrictions

## Browser Cookies Usage

### Loading Cookies from Browser

yt-dlp can load cookies from your browser to bypass login requirements and access restrictions:

```bash
# From Chrome
yt-dlp --cookies-from-browser chrome "VIDEO_URL"

# From Microsoft Edge
yt-dlp --cookies-from-browser edge "VIDEO_URL"

# From Firefox
yt-dlp --cookies-from-browser firefox "VIDEO_URL"
```

### Supported Browsers
- Chrome
- Microsoft Edge
- Firefox
- Safari
- Opera

### Common Issues with Cookies

**Issue:** Failed to find browser cookies database
**Solution:** Ensure the browser is installed and you're using the correct browser name

**Issue:** Cookie file format error
**Solution:** Use the `--cookies-from-browser` parameter instead of directly specifying cookie files

## FFmpeg Installation and Usage

### Why FFmpeg?
FFmpeg is required for:
- Merging separate video and audio files
- Converting video formats
- Extracting audio from videos

### Installation

#### macOS (using Homebrew):
```bash
brew install ffmpeg
```

#### Windows (using Chocolatey):
```bash
choco install ffmpeg
```

#### Linux (Ubuntu/Debian):
```bash
sudo apt update && sudo apt install ffmpeg
```

### Merging Video and Audio

If yt-dlp downloads separate video and audio files:

```bash
ffmpeg -i "video_file.mp4" -i "audio_file.m4a" -c:v copy -c:a copy "output.mp4"
```

## Site-Specific Download Strategies

### YouTube
- **403 Error:** Use browser cookies and adjust user-agent
- **Age-Restricted Videos:** Use browser cookies with login
- **Live Streams:** Add `--wait-for-video` parameter

### Bilibili
- **Member-Only Videos:** Use browser cookies with logged-in account
- **High Quality Formats:** May require membership
- **Anthology Videos:** yt-dlp will automatically handle playlists

### Other Sites
- **Facebook/Instagram:** May require login credentials via cookies
- **Vimeo:** Some videos may have download restrictions
- **DailyMotion:** Generally easier to download without restrictions

## Updated Examples

### Example 1: Downloading YouTube video with cookies

**User Input:**
```
Download https://youtu.be/dQw4w9WgXcQ using browser cookies
```

**Skill Action:**
```bash
# Using Chrome cookies
yt-dlp --cookies-from-browser chrome "https://youtu.be/dQw4w9WgXcQ"

# Using Edge cookies (if Chrome fails)
yt-dlp --cookies-from-browser edge "https://youtu.be/dQw4w9WgXcQ"
```

### Example 2: Downloading Bilibili video and merging

**User Input:**
```
Download https://www.bilibili.com/video/BV1e5fZBNExs/
```

**Skill Action:**
```bash
# Download video
yt-dlp "https://www.bilibili.com/video/BV1e5fZBNExs/"

# If files are separate, merge them
ffmpeg -i "video_file.mp4" -i "audio_file.m4a" -c:v copy -c:a copy "output.mp4"

# Clean up temporary files
rm "video_file.mp4" "audio_file.m4a"
```

### Example 3: Handling YouTube 403 error

**User Input:**
```
Download https://youtu.be/6_oFhJvN8QE?si=1x77k7oKXpul8jzN (failing with 403 error)
```

**Skill Action:**
```bash
# Try with browser cookies and adjusted settings
HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= yt-dlp --cookies-from-browser edge --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36" --sleep-interval 10 "https://youtu.be/6_oFhJvN8QE?si=1x77k7oKXpul8jzN"
```

## Troubleshooting Checklist

1. **yt-dlp Installation:**
   - Verify yt-dlp is installed and up-to-date
   - Run `yt-dlp --version` to check

2. **Network Issues:**
   - Disable proxies if causing problems
   - Try different network connections

3. **Access Restrictions:**
   - Use browser cookies for logged-in access
   - Check if video is region-restricted

4. **File Merging:**
   - Install ffmpeg for merging capabilities
   - Use `ffmpeg` command to combine video and audio

5. **Update yt-dlp:**
   - Run `pip install --upgrade yt-dlp` regularly
   - Newer versions may handle anti-scraping measures better

By following these guidelines, you should be able to successfully download videos from most websites, even when encountering common issues like YouTube's 403 errors.