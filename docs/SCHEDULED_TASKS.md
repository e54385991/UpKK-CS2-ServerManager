# Scheduled Tasks (计划任务)

## Overview

The Scheduled Tasks feature allows you to automate server operations on a regular schedule. You can configure tasks to run daily, weekly, at specific intervals, or using cron expressions.

## Features

- **Multiple Schedule Types**: Daily, Weekly, Interval, and Cron expressions
- **Flexible Actions**: Restart, Start, Stop, Update, and Validate server operations
- **Task Management**: Enable/disable tasks without deleting them
- **Execution Tracking**: View last run time, next run time, run count, and status
- **Error Logging**: Track failures and view error messages

## API Endpoints

### Create Scheduled Task
```
POST /api/scheduled-tasks/{server_id}
```

Request body:
```json
{
  "name": "Daily Restart",
  "action": "restart",
  "enabled": true,
  "schedule_type": "daily",
  "schedule_value": "14:30"
}
```

### List Scheduled Tasks
```
GET /api/scheduled-tasks/{server_id}
```

### Get Scheduled Task
```
GET /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

### Update Scheduled Task
```
PUT /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

Request body (all fields optional):
```json
{
  "name": "Updated Task Name",
  "enabled": false,
  "schedule_type": "weekly",
  "schedule_value": "SUN:02:00"
}
```

### Delete Scheduled Task
```
DELETE /api/scheduled-tasks/{server_id}/tasks/{task_id}
```

### Toggle Scheduled Task
```
POST /api/scheduled-tasks/{server_id}/tasks/{task_id}/toggle
```

## Schedule Types and Values

### Daily
Runs at a specific time every day.
- **Format**: `HH:MM` (24-hour format)
- **Example**: `14:30` (runs at 2:30 PM every day)

### Weekly
Runs at a specific time on a specific day of the week.
- **Format**: `DAY:HH:MM`
- **Days**: MON, TUE, WED, THU, FRI, SAT, SUN
- **Example**: `SUN:02:00` (runs at 2:00 AM every Sunday)

### Interval
Runs repeatedly at a fixed interval.
- **Format**: Number of seconds
- **Example**: `3600` (runs every hour)
- **Example**: `86400` (runs every 24 hours)

### Cron (Future Enhancement)
Cron expression support (not yet fully implemented).
- **Format**: `MIN HOUR DAY MONTH WEEKDAY`
- **Example**: `0 2 * * *` (runs at 2:00 AM every day)

## Available Actions

- `restart`: Restart the server
- `start`: Start the server
- `stop`: Stop the server
- `update`: Update the server files
- `validate`: Validate server files

## Examples

### Daily Server Restart
```json
{
  "name": "Daily 3 AM Restart",
  "action": "restart",
  "enabled": true,
  "schedule_type": "daily",
  "schedule_value": "03:00"
}
```

### Weekly Server Update
```json
{
  "name": "Sunday Morning Update",
  "action": "update",
  "enabled": true,
  "schedule_type": "weekly",
  "schedule_value": "SUN:04:00"
}
```

### Hourly Status Check
```json
{
  "name": "Hourly Validation",
  "action": "validate",
  "enabled": true,
  "schedule_type": "interval",
  "schedule_value": "3600"
}
```

## Task Execution

- Tasks are checked every 30 seconds
- Only enabled tasks will be executed
- If a task is already running, it won't start again
- After execution, the next run time is automatically calculated
- Execution history (last run, status, error) is tracked

## Security

- Schedule values are validated to prevent command injection
- Only valid actions (restart, start, stop, update, validate) are allowed
- Tasks can only be managed by the server owner (user authentication required)

## Localization

The feature supports both English and Chinese localization:
- English: `/static/locales/en-US.json`
- Chinese: `/static/locales/zh-CN.json`

## Service Management

The scheduled task service:
- Starts automatically on application startup
- Stops gracefully on application shutdown
- Calculates next run times for all enabled tasks on startup
- Runs tasks in the background without blocking other operations
