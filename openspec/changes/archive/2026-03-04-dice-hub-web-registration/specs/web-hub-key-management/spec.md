## ADDED Requirements

### Requirement: Master can view current API Key
The robot SHALL display the current API Key to the Master.

#### Scenario: View API Key
- **WHEN** Master sends `.hub key` command and robot has api_key
- **THEN** robot displays the current api_key to Master
- **AND** message format: "当前机器人 API Key: xxx"

#### Scenario: View API Key without registration
- **WHEN** Master sends `.hub key` but robot has no api_key
- **THEN** robot replies with message suggesting `.hub register` first

---

### Requirement: Master can view robot status
The robot SHALL display its current registration and connection status.

#### Scenario: View robot status (registered and online)
- **WHEN** Master sends `.hub status` command (if implemented)
- **THEN** robot displays: registration status, API Key (masked), online status, last heartbeat time

#### Scenario: View robot status (not registered)
- **WHEN** Master sends `.hub status` but robot has no api_key
- **THEN** robot displays: "未注册，请使用 .hub register 注册"

---

### Requirement: Configuration via command
Master SHALL be able to configure the API URL via command.

#### Scenario: Set API URL
- **WHEN** Master sends `.hub url https://example.com/api` command
- **THEN** robot stores the URL in `dicehub_api_url` config
- **AND** robot replies with success message

#### Scenario: View current API URL
- **WHEN** Master sends `.hub url` command (without value)
- **THEN** robot displays current configured API URL
