# JudgeScore Model Documentation

## Overview

The `JudgeScore` class represents a judge's evaluation of a team during a hackathon event. It contains scoring criteria across multiple dimensions, feedback, and metadata about the scoring session.

## Class Location

`model/judge_score.py`

## Primary Usage

The `JudgeScore` model is primarily used by:
- `api/judging/judging_service.py` - Service layer for judging operations
- `db/firestore.py` - Database persistence layer
- `db/db.py` - Database interface abstraction

## Model Structure

### Identification Fields
- `id` (str): Unique identifier for the score record (set by database on insert)
- `judge_id` (str): ID of the judge who submitted the score
- `team_id` (str): ID of the team being judged
- `event_id` (str): ID of the hackathon event
- `round` (str): Judging round ('round1' or 'round2')

### Scoring Criteria (1-5 points each)

#### Scope Category
- `scope_impact` (int): Impact of the project
- `scope_complexity` (int): Technical complexity

#### Documentation Category
- `documentation_code` (int): Code documentation quality
- `documentation_ease` (int): Ease of understanding documentation

#### Polish Category
- `polish_work_remaining` (int): Amount of work remaining (inverse scoring)
- `polish_can_use_today` (int): Readiness for immediate use

#### Security Category
- `security_data` (int): Data security measures
- `security_role` (int): Role-based access control

#### Special Categories
- `accessibility` (int, optional): Accessibility features (1-5 points)
  - **Note**: This is a special category prize and is NOT included in the total score calculation

### Additional Fields
- `total_score` (int): Calculated sum of 8 core criteria (excludes accessibility)
- `feedback` (str): Optional written feedback from the judge
- `is_draft` (bool): Whether this is a draft (autosaved) or final submission
- `submitted_at` (datetime): Timestamp when the score was officially submitted (null for drafts)

### Timestamp Fields

#### `created_at` (datetime)
**How it's set**: Automatically set by the database layer when a new score record is first inserted.

- **Location**: `db/firestore.py:736` in `insert_judge_score()`
- **Value**: `datetime.now()` at the moment of insertion
- **When it occurs**:
  - When a judge submits a score for the first time for a specific team/event/round combination
  - Only set once during the initial insert operation

#### `updated_at` (datetime)
**How it's set**: Automatically set by the database layer on both insert and update operations.

- **Location**:
  - `db/firestore.py:737` in `insert_judge_score()` (on initial creation)
  - `db/firestore.py:749` in `update_judge_score()` (on subsequent updates)
  - `db/firestore.py:760` in `upsert_judge_score()` (preserves `created_at`, sets new `updated_at`)
- **Value**: `datetime.now()` at the moment of the operation
- **When it occurs**:
  - Set to current time when a score is first created
  - Updated to current time whenever the score is modified
  - **Special case for drafts**: When saving draft scores through the API (`api/judging/judging_service.py:403`), `updated_at` can be explicitly set from the client timestamp

**Important Implementation Detail**: The timestamp management is handled at the **database layer** (Firestore implementation), not in the model itself. The model class initializes these fields to `None`, but the actual values are set by `FirestoreDatabaseInterface` methods.

## Score Calculation

The `calculate_total_score()` method (line 62-81) computes the total score by summing 8 criteria:
1. scope_impact
2. scope_complexity
3. documentation_code
4. documentation_ease
5. polish_work_remaining
6. polish_can_use_today
7. security_data
8. security_role

**Note**: The `accessibility` score is intentionally excluded from the total as it's tracked separately for a special category prize.

## API Format Conversion

### Frontend Format (camelCase)
The model provides conversion methods for API communication:

- `to_api_format()` (line 83-96): Converts internal snake_case to frontend camelCase
- `from_api_format()` (line 98-112): Converts frontend camelCase to internal snake_case

Example API format:
```python
{
    "scopeImpact": 4,
    "scopeComplexity": 5,
    "documentationCode": 3,
    "documentationEase": 4,
    "polishWorkRemaining": 3,
    "polishCanUseToday": 4,
    "securityData": 5,
    "securityRole": 4,
    "accessibility": 3,
    "total": 32  # Sum of 8 criteria (excludes accessibility)
}
```

## Persistence Strategy

### Upsert Pattern
The database uses an **upsert pattern** (`db/firestore.py:753`) that:
1. Checks for an existing score with the same `(judge_id, team_id, event_id, round, is_draft)` combination
2. If found: Updates the existing record (preserves `created_at`, updates `updated_at`)
3. If not found: Inserts a new record (sets both `created_at` and `updated_at`)

This ensures:
- No duplicate scores for the same judge/team/event/round/draft combination
- Proper timestamp tracking across updates
- Seamless handling of both new submissions and revisions

## Draft vs. Final Scores

### Draft Scores
- `is_draft = True`
- `submitted_at = None`
- Used for autosave functionality
- Can be partially complete
- Retrieved with `fetch_judge_score(..., is_draft=True)`

### Final Scores
- `is_draft = False`
- `submitted_at` set to submission timestamp
- All required fields must be complete (validation in `judging_service.py:289-304`)
- Retrieved with `fetch_judge_score(..., is_draft=False)` (default)

## Common Operations

### Submit Score
Service: `submit_judge_score()` in `judging_service.py:281`
- Validates all required fields are present and in range (1-5)
- Sets `is_draft = False`
- Sets `submitted_at` timestamp
- Calculates total score
- Uses `upsert_judge_score()` to save

### Save Draft
Service: `save_draft_score()` in `judging_service.py:385`
- Allows partial completion
- Sets `is_draft = True`
- Leaves `submitted_at = None`
- Optionally sets `updated_at` from client timestamp
- Only calculates total if all scores present

### Retrieve Score
Service: `get_individual_judge_score()` in `judging_service.py:640`
- Fetches specific score by judge/team/event/round
- Can retrieve draft or final version
- Returns formatted response with timestamps

### Bulk Retrieval
Service: `get_bulk_judge_scores()` in `judging_service.py:852`
- Fetches all scores for an event and round
- Includes team and judge name resolution
- Provides summary statistics

## Serialization

### serialize() (line 52-60)
Converts the object to a dictionary containing all non-callable attributes (excludes methods).
Used by database layer when persisting to Firestore.

### deserialize() (line 27-50)
Class method that creates a `JudgeScore` object from a dictionary.
Used by database layer when loading from Firestore.

## Validation Rules

When submitting final scores (not drafts), the following validation applies:
- All 8 core criteria must be present
- Each score must be an integer between 1 and 5 (inclusive)
- `accessibility` is optional (not validated as required)

## Query Patterns

### By Judge and Event
```python
fetch_judge_scores_by_judge_and_event(judge_id, event_id)
```
Returns all non-draft scores for a judge in an event.

### By Event and Round
```python
fetch_judge_scores_by_event_and_round(event_id, round_name)
```
Returns all non-draft scores for a specific round of an event.

### Specific Score Lookup
```python
fetch_judge_score(judge_id, team_id, event_id, round_name, is_draft=False)
```
Returns a specific score (or None if not found).

## Related Models

- `JudgeAssignment` - Links judges to teams for specific rounds
- `JudgePanel` - Groups judges for round 2 demo sessions
- `User` - Judge and team member information
- `Team` - Team being judged

## Example Usage

```python
# Create a new score from API data
score = JudgeScore.from_api_format(api_data)
score.judge_id = "judge123"
score.team_id = "team456"
score.event_id = "event789"
score.round = "round1"
score.feedback = "Great work on accessibility features!"
score.is_draft = False
score.submitted_at = datetime.now()

# Calculate total
score.calculate_total_score()  # Sets total_score to sum of 8 criteria

# Save to database (timestamps set automatically by DB layer)
saved_score = upsert_judge_score(score)

# Retrieve later
retrieved = fetch_judge_score(
    judge_id="judge123",
    team_id="team456",
    event_id="event789",
    round_name="round1",
    is_draft=False
)

# Convert for API response
api_response = retrieved.to_api_format()
```

## Key Implementation Notes

1. **Timestamp Management**: Timestamps are managed by the database layer, not the model itself
2. **Total Score Calculation**: Must be explicitly called via `calculate_total_score()`
3. **Accessibility Exclusion**: The accessibility score is tracked but not included in totals
4. **Upsert Pattern**: Prevents duplicate scores while allowing updates
5. **Draft Functionality**: Enables autosave without triggering submission
6. **Validation Location**: Field validation happens in the service layer, not the model
