# Feature: Per-Chat LLM Model Selection

## Overview
Allow users to select different LLM models for individual chat conversations, enabling them to choose the most appropriate model based on their specific needs (e.g., speed vs. quality, cost, capabilities).

## Current State
- The application currently uses a single global LLM model configured via environment variable (`llm_model` in `.env`)
- Model is set once at server startup and applies to all conversations
- Similar per-conversation customization exists for skills (skill overrides)

## Requirements

### Functional Requirements

#### FR1: Model Selection UI
- Users must be able to select an LLM model when starting a new conversation
- A dropdown/selector should be displayed near the prompt input area
- The currently selected model should be clearly visible

#### FR2: Model Persistence
- The selected model must persist throughout a conversation chain
- When continuing an existing conversation, the model used in that conversation should be automatically selected
- Starting a new conversation should reset to the default model

#### FR3: Model Configuration
- Administrators must be able to configure the list of available models
- A default model must be configurable in application settings
- The settings modal should include a section for model configuration

#### FR4: Model Information Display
- Each conversation should display which model is being used (optional: show in sidebar as badge)
- Individual messages may optionally indicate which model generated them

#### FR5: Backward Compatibility
- Existing conversations without a model selection should default to the global configured model
- The application must continue to work if no model is explicitly selected

### Technical Requirements

#### TR1: Backend Data Model
- Add `llm_model: str | None` field to `RunContext` in `app/models.py`
- `None` value indicates using the global default model
- Field must be persisted in `runs.json`

#### TR2: Backend API
- Modify `POST /api/agent/run` to accept optional `llm_model` parameter
- Add `GET /api/models` endpoint returning list of available models
- Update `GET /api/settings` to include model configuration

#### TR3: Model Inheritance
- Child runs (follow-up messages) must inherit the parent run's model selection
- Inheritance logic should mirror existing skill overrides pattern

#### TR4: Frontend State Management
- Add `availableModels` state to App.jsx (loaded on mount)
- Add `selectedModel` state to track current selection
- Reset model selection appropriately on "New Chat"

#### TR5: Frontend UI Components
- Model selector component in conversation input area
- Model configuration section in settings modal
- Optional: Model indicator badges in sidebar

### Non-Functional Requirements

#### NFR1: Consistency
- Follow existing patterns from skill overrides feature
- Maintain consistent UI/UX with rest of application

#### NFR2: Performance
- Model selection should not introduce noticeable latency
- Available models list can be cached in frontend

#### NFR3: Usability
- Model selection should be intuitive and not clutter the interface
- Default model should work without user intervention

## Implementation Approach

### Pattern to Follow
The implementation should mirror the existing **skill overrides** feature:
1. Global default setting
2. Per-run override field
3. Parent-to-child inheritance
4. API endpoints for configuration
5. UI components for selection

### Key Files to Modify

**Backend:**
- `app/models.py` - Add model field to RunContext
- `app/main.py` - Update start_run() and _build_workflow()
- `app/config.py` - Add available models list
- `app/settings_store.py` - Add model configuration fields

**Frontend:**
- `src/App.jsx` - Add model state management
- `src/ConversationFeed.jsx` - Add model selector UI
- `src/SkillsModal.jsx` - Add model configuration section
- `src/Sidebar.jsx` - Optional: Add model indicators

### Implementation Phases

#### Phase 1: Backend Foundation
1. Update data models and persistence
2. Implement model inheritance logic
3. Add API endpoints for model management

#### Phase 2: Frontend Integration
1. Create model selector UI component
2. Add state management for model selection
3. Integrate with chat submission flow

#### Phase 3: Configuration & Polish
1. Add model configuration to settings modal
2. Add visual indicators throughout UI
3. Handle edge cases and backward compatibility

#### Phase 4: Testing & Validation
1. Test model selection on new chats
2. Test inheritance in conversation chains
3. Test backward compatibility with existing data
4. Test default fallback behavior

## Success Criteria
- Users can select from multiple LLM models when starting a conversation
- Selected model persists throughout the conversation chain
- Existing conversations continue to work unchanged
- Settings allow configuration of available models and default
- UI clearly indicates which model is in use

## Future Enhancements (Out of Scope)
- Mid-conversation model switching
- Per-message model override
- Model capability validation
- Cost estimation per model
- Model performance metrics
