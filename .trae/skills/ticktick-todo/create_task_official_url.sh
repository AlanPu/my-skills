#!/bin/bash

# Shell script to create a task in TickTick using official URL Scheme
# Usage: bash create_task_official_url.sh "任务标题" "时间" "执行人" "清单"
# Example: bash create_task_official_url.sh "弹琴" "明天 08:00" "~提子" "🧚提子"

# Default values
DEFAULT_TITLE="英语课"
DEFAULT_TIME="今天"
DEFAULT_ASSIGNEE=""
DEFAULT_LIST=""

# Parse command line arguments
TASK_TITLE=${1:-$DEFAULT_TITLE}
TASK_TIME=${2:-$DEFAULT_TIME}
TASK_ASSIGNEE=${3:-$DEFAULT_ASSIGNEE}
TASK_LIST=${4:-$DEFAULT_LIST}

# Process time information
if [[ "$TASK_TIME" == *"明天"* ]]; then
    # Tomorrow's date
    TASK_DUE_DATE=$(date -v+1d +"%Y-%m-%d")
    # Extract time if provided
    if [[ "$TASK_TIME" =~ 明天[[:space:]]*([0-9]{1,2}:[0-9]{2}) ]]; then
        TASK_DUE_TIME=${BASH_REMATCH[1]}
    else
        TASK_DUE_TIME=""
    fi
elif [[ "$TASK_TIME" == *"今天"* ]]; then
    # Today's date
    TASK_DUE_DATE=$(date +"%Y-%m-%d")
    # Extract time if provided
    if [[ "$TASK_TIME" =~ 今天[[:space:]]*([0-9]{1,2}:[0-9]{2}) ]]; then
        TASK_DUE_TIME=${BASH_REMATCH[1]}
    else
        TASK_DUE_TIME=""
    fi
else
    # Default to today
    TASK_DUE_DATE=$(date +"%Y-%m-%d")
    TASK_DUE_TIME=""
fi

# Format task content
if [[ -n "$TASK_DUE_TIME" ]]; then
    TASK_CONTENT="$TASK_TITLE $TASK_DUE_DATE $TASK_DUE_TIME"
else
    TASK_CONTENT="$TASK_TITLE $TASK_DUE_DATE"
fi

# Add assignee if provided
if [[ -n "$TASK_ASSIGNEE" ]]; then
    TASK_CONTENT="$TASK_CONTENT $TASK_ASSIGNEE"
fi

# Format startDate in ISO 8601 format if time is provided
if [[ -n "$TASK_DUE_TIME" ]]; then
    START_DATE_TIME="${TASK_DUE_DATE}T${TASK_DUE_TIME}:00.000"
    ALL_DAY=false
else
    START_DATE_TIME="${TASK_DUE_DATE}T00:00:00.000"
    ALL_DAY=true
fi

# URL encode the parameters
ENCODED_TITLE=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_TITLE'))")
ENCODED_CONTENT=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_CONTENT'))")
ENCODED_LIST=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$TASK_LIST'))")

# Create the URL using official format
if [[ -n "$START_DATE_TIME" ]]; then
    if [[ -n "$TASK_LIST" ]]; then
        ticktick_url="ticktick://x-callback-url/v1/add_task?title=$ENCODED_TITLE&startDate=$START_DATE_TIME&allDay=$ALL_DAY&content=$ENCODED_CONTENT&list=$ENCODED_LIST"
    else
        ticktick_url="ticktick://x-callback-url/v1/add_task?title=$ENCODED_TITLE&startDate=$START_DATE_TIME&allDay=$ALL_DAY&content=$ENCODED_CONTENT"
    fi
else
    if [[ -n "$TASK_LIST" ]]; then
        ticktick_url="ticktick://x-callback-url/v1/add_task?title=$ENCODED_TITLE&content=$ENCODED_CONTENT&list=$ENCODED_LIST"
    else
        ticktick_url="ticktick://x-callback-url/v1/add_task?title=$ENCODED_TITLE&content=$ENCODED_CONTENT"
    fi
fi

# Open the URL
echo "Opening official TickTick URL: $ticktick_url"
open "$ticktick_url"

echo "Task creation window should open in TickTick."
echo "Please check TickTick and confirm the task details."
echo "\nTask details:"
echo "- Title: $TASK_TITLE"
echo "- Time: $TASK_TIME"
if [[ -n "$TASK_ASSIGNEE" ]]; then
    echo "- Assignee: $TASK_ASSIGNEE"
fi
if [[ -n "$TASK_LIST" ]]; then
    echo "- List: $TASK_LIST"
fi
