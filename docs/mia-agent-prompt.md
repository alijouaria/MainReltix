# Mia · tenant complaint calls

This branch uses the ElevenLabs browser client for resident-initiated complaint calls. The energy supplier call remains a local simulation.

## Agent configuration for this flow

Use `{{first_message}}` as the greeting and `{{call_reason}}` for context. The complaint button supplies `problem_report`. Enable the client tools `save_note(note)` and `handoff_to_human(reason)`, plus the provider’s end-conversation tool.

## Complaint conversation

Ask the tenant what happened and which part of the flat or service is affected. Ask for missing details one at a time. Use `save_note` to record the report on the flat and append any clarification to that ticket. For urgent issues or requests for a person, use `handoff_to_human`. Confirm only what the tool actually saved.

The resident sees the complaint in their portal. They can explicitly share it with the supplier of an accepted order. Supplier replies return to the same complaint. The portal handles offer selection and consent.
