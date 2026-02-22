#!/bin/bash

# Shell script to create a task in TickTick using official URL Scheme

# Task details
TASK_TITLE="上英语课"
TASK_DUE_DATE="$(date -v+1d +"%Y-%m-%d")" # Tomorrow's date in YYYY-MM-DD format
TASK_DUE_TIME="09:00"
TASK_ASSIGNEE="提子"
TASK_CONTENT="$TASK_TITLE $TASK_DUE_DATE $TASK_DUE_TIME $TASK_ASSIGNEE"

# Format startDate in ISO 8601 format
START_DATE_TIME="${TASK_DUE_DATE}T${TASK_DUE_TIME}:00.000"

# URL encode the parameters
ENCODED_TITLE=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_TITLE'))")
ENCODED_CONTENT=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_CONTENT'))")
ENCODED_ASSIGNEE=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_ASSIGNEE'))")

# Create the URL using official format
ticktick_url="ticktick://x-callback-url/v1/add_task?title=$ENCODED_TITLE&startDate=$START_DATE_TIME&allDay=false&content=$ENCODED_CONTENT"

# Open the URL
echo "Opening official TickTick URL: $ticktick_url"
open "$ticktick_url"

echo "Task creation window should open in TickTick."
echo "Please check TickTick and confirm the task details."
echo "\nTask details:"
echo "- Title: $TASK_TITLE"
echo "- Due: $TASK_DUE_DATE $TASK_DUE_TIME"
echo "- Assignee: $TASK_ASSIGNEE"
