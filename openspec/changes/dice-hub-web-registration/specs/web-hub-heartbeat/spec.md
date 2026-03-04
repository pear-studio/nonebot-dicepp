## ADDED Requirements

### Requirement: Robot sends periodic heartbeat
The robot SHALL send periodic heartbeat requests to the website to maintain online status.

#### Scenario: Automatic heartbeat in production mode
- **WHEN** robot is in production mode (not testing) and has valid api_key
- **THEN** robot sends heartbeat every 3 minutes
- **AND** robot's online status is updated on the website

#### Scenario: Automatic heartbeat in test mode
- **WHEN** robot is in test mode and has valid api_key
- **THEN** robot sends heartbeat every 10 seconds
- **AND** robot's online status is updated on the website

#### Scenario: Manual heartbeat trigger
- **WHEN** Master sends `.hub online` command
- **THEN** robot immediately sends heartbeat request
- **AND** robot replies with success/failure message

#### Scenario: Heartbeat without API Key
- **WHEN** Master sends `.hub online` but robot has no api_key
- **THEN** robot replies with error message suggesting `.hub register` first

#### Scenario: Heartbeat with network failure
- **WHEN** heartbeat request fails due to network error
- **THEN** robot logs the error
- **AND** robot retries on next scheduled heartbeat
- **AND** robot does NOT change local online status (unknown)

#### Scenario: Heartbeat with API error
- **WHEN** heartbeat request fails due to API error (e.g., invalid api_key)
- **THEN** robot logs the error
- **AND** robot replies to Master with error message

---

### Requirement: Robot detects offline status
The robot SHALL handle the case when it cannot connect to the website.

#### Scenario: Robot cannot reach website
- **WHEN** heartbeat fails for 3 consecutive times
- **THEN** robot marks website as unreachable locally
- **AND** robot continues attempting heartbeats
- **AND** robot continues functioning normally (heartbeat failure does not affect other features)
