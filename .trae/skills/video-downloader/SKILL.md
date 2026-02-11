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
- And many more

## Requirements

- yt-dlp must be installed on the system
- For installation instructions, visit: https://github.com/yt-dlp/yt-dlp#installation

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