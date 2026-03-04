## ADDED Requirements

### Requirement: Robot can register via API
The robot SHALL be able to register itself with the website by sending a POST request to the registration endpoint.

#### Scenario: Successful registration
- **WHEN** Master sends `.hub register` command and `dicehub_api_url` is configured
- **THEN** robot sends POST request to `/api/bots/register` with bot_id, nickname, master_id, version
- **AND** robot receives response with api_key and bot_id
- **AND** robot stores api_key locally
- **AND** robot replies to Master with success message including api_key

#### Scenario: Registration without API URL configured
- **WHEN** Master sends `.hub register` command but `dicehub_api_url` is not configured
- **THEN** robot replies with error message asking to configure API URL first

#### Scenario: Registration fails due to network error
- **WHEN** Master sends `.hub register` command but network request fails
- **THEN** robot replies with error message including failure reason

#### Scenario: Registration fails due to API error
- **WHEN** Master sends `.hub register` command but API returns error
- **THEN** robot replies with error message from API

---

### Requirement: Robot stores API Key locally
The robot SHALL store the received API Key in local configuration.

#### Scenario: API Key storage
- **WHEN** robot receives api_key from registration API
- **THEN** robot stores api_key in `dicehub_api_key` config
- **AND** api_key persists across robot restarts

---

### Requirement: Robot re-registers if already registered
If the robot has already been registered, it SHALL update its information on the website.

#### Scenario: Re-registration with existing API Key
- **WHEN** robot has existing api_key and Master sends `.hub register`
- **THEN** robot sends PUT request to update bot info with existing api_key
- **AND** robot receives new api_key in response
- **AND** robot updates stored api_key
