## ADDED Requirements

### Requirement: Master can view online robot list
The robot SHALL be able to fetch and display the list of online robots from the website.

#### Scenario: View online robot list
- **WHEN** Master sends `.hub list` command and robot has valid api_key
- **THEN** robot fetches online robot list from website API
- **AND** robot displays list to Master including: nickname, bot_id, online status, version

#### Scenario: View robot list without API Key
- **WHEN** Master sends `.hub list` but robot has no api_key
- **THEN** robot replies with error message suggesting `.hub register` first

#### Scenario: Robot list is empty
- **WHEN** Master sends `.hub list` and website returns empty list
- **THEN** robot displays message "暂无在线机器人"

#### Scenario: View robot list with network failure
- **WHEN** Master sends `.hub list` but network request fails
- **THEN** robot replies with error message including failure reason

---

### Requirement: Robot refreshes list periodically
The robot SHALL periodically fetch the online robot list to keep it updated.

#### Scenario: Periodic list refresh
- **WHEN** robot has valid api_key
- **THEN** robot fetches online robot list every 10 minutes
- **AND** robot caches the list locally for quick access
