# Cog How-To Guide

This file is the user-facing companion to `README.md`.

Each section explains three things:
- what the cog is for
- how members or staff normally use it
- rules or limitations that matter in practice

## `botadmin.py`

Overview: Bot control for the owner or bot admins.

Slash commands: `/shutdown`, `/restart`, `/reload_cog`, `/git_pull`.

How to use: Use `/shutdown` to stop the bot, `/restart` to restart it after maintenance or a bad state, `/reload_cog` to reload a specific cog, and `/git_pull` to pull the latest code on the server.

Rules and notes: Treat these as live-service controls. They are not normal member commands and should only be used when you really want to interrupt or reload the bot.

## `rosterizer.py`

Overview: Controls when the roster can or cannot be edited.

Slash commands: `/lockroster`, `/unlockroster`.

How to use: Use `/lockroster` when a roster is final and `/unlockroster` when changes are allowed again.

Rules and notes: Locking should be used once staff want consistency. Unlock only when changes are genuinely open again, otherwise people will assume the published roster is still flexible.

## `quick_exit.py`

Overview: Supports the fast leave or exit path for members.

Slash commands: none exposed here in the current bot loadout.

How to use: Use this when someone needs the short offboarding route instead of a manual staff conversation.

Rules and notes: This is an operational flow, not a public feature to spam. It should follow whatever your server already treats as the correct leaving process.

## `bulkrole.py`

Overview: Applies prebuilt role bundles in one step.

Slash commands: `/bulk-role`.

How to use: Use `/bulk-role` to give one user a saved preset of roles, usually during onboarding or qualification updates.

Rules and notes: Only use presets that match your staff process. Bulk role tools are fast, so mistakes spread fast too.

## `certify.py`

Overview: Generates a certificate image for a member.

Slash commands: `/certify`.

How to use: Use `/certify` and fill in the requested details.

Rules and notes: Check the spelling, date, and recipient before sending. This is one of the cogs where clean input matters more than speed.

## `recruitform.py`

Overview: Handles recruit intake and the review flow.

Slash commands: none exposed here in the current bot loadout.

How to use: Direct recruits into the form flow and let staff review the output inside Discord.

Rules and notes: This should be the standard path if you want consistent recruit handling. Avoid splitting people between multiple unofficial intake methods.

## `EmbedManager.py`

Overview: Keeps important static embeds up to date.

Slash commands: none. Text command: `!sync_embeds`.

How to use: Use `!sync_embeds` after changing managed embed text, layout, or destinations.

Rules and notes: This is mainly a maintenance tool. Members usually read the embeds rather than interact with this cog directly.

## `SquadUp.py`

Overview: Creates event signup posts for squads and crews.

Slash commands: `/squadup`, `/squadupmulti`, `/crewup`.

How to use: Use `/squadup` for one squad, `/squadupmulti` for several squads, and `/crewup` for armour crew signups.

Rules and notes: Pick the command that matches the event structure. If the event is simple, keep the signup simple. Overcomplicated signup boards make people stop responding.

## `eventscalendar.py`

Overview: Builds and refreshes the public Discord event display.

Slash commands: none exposed here in the current bot loadout.

How to use: Members read it as the current event board. Staff keep scheduled events accurate and the cog refreshes the display.

Rules and notes: Treat it as the public source for upcoming events. If event titles or times are wrong in Discord scheduled events, this display will mirror that.

## `BirthdayCog.py`

Overview: Lets members store birthdays and shows birthday lists.

Slash commands: `/setbirthday`, `/removebirthday`, `/birthdaysplease`.

How to use: Members use `/setbirthday`, `/removebirthday`, and `/birthdaysplease`.

Rules and notes: Only set your own birthday unless staff explicitly manage these for someone else. If age display is optional, think before making that visible.

## `contentfeed.py`

Overview: Pushes configured content automatically and allows manual override.

Slash commands: `/forcecontent`.

How to use: Let the scheduled feed run normally. Use `/forcecontent` only when staff need to publish immediately.

Rules and notes: Manual forcing is for exceptions, not normal posting. If overused, it defeats the point of having a feed schedule.

## `discordgreeting.py`

Overview: Handles welcome and greeting behavior for new arrivals.

Slash commands: none exposed here in the current bot loadout.

How to use: New members experience it automatically. Staff mainly maintain the channels and wording around it.

Rules and notes: Keep the greeting flow clear and short. If too much information lands at once, new members will miss the important bits.

## `echo.py`

Overview: Lets approved users send a controlled message through the bot.

Slash commands: `/7drecho`.

How to use: Use `/7drecho` in the channel where you want the message posted.

Rules and notes: This is best for repeat notices, standardized announcements, or places where you want limited people to publish without wider permissions.

## `mapvote.py`

Overview: Runs the map voting workflow and persistent vote embed.

Slash commands: `/mapvote_enable`, `/mapvote_disable`.

How to use: Members vote through the existing map vote message or controls when the system is active.

Rules and notes: Use this as the main map voting route instead of mixing in random manual polls. The slash commands are staff controls for turning the system on or off, not member vote commands.

## `HLLInfLeaderboard.py`

Overview: Shows infantry top scores.

Slash commands: `/hllhighs-inftopscores`, `/hllhighs-infscoreadmin`.

How to use: Use `/hllhighs-inftopscores` to view the all-time infantry highs. Staff can use `/hllhighs-infscoreadmin` to correct or remove bad entries.

Rules and notes: This is a read-first scoreboard for members. Staff admin tools should only be used when correcting bad entries or removing invalid scores.

## `HLLArmLeaderboard.py`

Overview: Shows armour crew top scores.

Slash commands: `/hllhighs-armtopscores`, `/hllhighs-armscoreadmin`.

How to use: Use `/hllhighs-armtopscores` to view the all-time armour highs. Staff can use `/hllhighs-armscoreadmin` to correct or remove bad entries.

Rules and notes: Same principle as the infantry board: members read, staff correct only when necessary.

## `gohamm.py`

Overview: Runs the Go Hamm feature and its related output.

Slash commands: none exposed here in the current bot loadout.

How to use: Use it in the established channel or workflow already built around that feature.

Rules and notes: This is one of the more server-specific cogs. Keep usage aligned with the community context it was built for.

## `GameMonCog.py`

Overview: Tracks game or server activity and drives related automated posts or state changes.

Slash commands: `/gamemon_test_hll`.

How to use: Members mostly interact with the feed or buttons it creates. Staff can use `/gamemon_test_hll` to post a test Hell Let Loose feed message and verify that the cog is working.

Rules and notes: Because this cog is automation-heavy, wrong channel config or wrong status assumptions can have visible side effects. Treat it like a live feed system.

## `multi_trainee_tracker.py`

Overview: Tracks trainee progress across multiple categories or training paths.

Slash commands: none exposed here in the current bot loadout.

How to use: Staff use it to record where someone is in the process instead of relying on scattered manual notes.

Rules and notes: Keep one clear source of truth. If staff track trainees in several places at once, this loses value quickly.

## `rollcall.py`

Overview: Runs scheduled roll calls and lets staff force them manually.

Slash commands: `/forcerollcall`.

How to use: Use `/forcerollcall` when a roll call needs to run immediately or a scheduled run was missed.

Rules and notes: Manual forcing should be the exception. If you use it too often, members stop trusting the schedule.

## `nameshame.py`

Overview: Handles the name-and-shame process and moderation-adjacent admin actions.

Slash commands: none exposed here in the current bot loadout.

How to use: Use it only in the designated channels and only for the moderation workflow it was designed for.

Rules and notes: This is not a casual member feature. Because it affects reputation and moderation records, keep usage controlled and consistent.

## `outofoffice.py`

Overview: Member-facing leave of absence system with scheduled role handling and optional automated replies.

Slash commands: `/loa`, `/loa-list`, `/loa_responseoff`, `/loa_responseon`, `/loa-delete`.

How to use: Use `/loa` to start the setup in DM. Use `/loa-list` to review saved entries, `/loa-delete` to remove one, `/loa_responseoff` to disable auto replies, and `/loa_responseon` to turn them back on.

Rules and notes: This cog has important nuance. Short LOAs are one-off, daily, or weekday-based absences that are 10 hours total or less. They still use the LOA role, but they do not post a confirmation in the LOA channel. Long LOAs are one continuous block longer than 10 hours and they do post a confirmation for SNCO review. Recurring LOAs must stay at 10 hours or less. Automated LOA replies only begin once you have been offline for 6 hours. Staff can still manually add or remove the LOA role, so the role itself can exist without a saved automated schedule.

## `wardiary.py`

Overview: Keeps a cleaner record of wars, events, or match history.

Slash commands: none exposed here in the current bot loadout.

How to use: Use it where the server wants a durable campaign or battle log rather than loose chat posts.

Rules and notes: This works best when the people entering records follow one consistent standard for names, dates, and result wording.

## `t17lookup.py`

Overview: Fixes or stores shared T17 IDs for members.

Slash commands: `/t17_overwrite`.

How to use: Staff use `/t17_overwrite` when automatic matching is wrong, missing, or inconsistent across systems.

Rules and notes: This is a correction tool. Use it when data matching fails, not as the first option for every member.

## `applyroletomessage.py`

Overview: Applies one role to everyone who reacted to one specific message with one specific emoji.

Slash commands: `/applyroletomessage`.

How to use: Use `/applyroletomessage` with the message link, the exact emoji, and the role to assign.

Rules and notes: This is an admin/staff tool, not a self-serve reaction-role system. The bot needs `Manage Roles`, and its top role must be above the target role. Use the exact message link and exact emoji or it will not match the intended reaction set.

## `hellorleaderboard.py`

Overview: Builds and posts the `hellor.pro` leaderboard for the configured Discord role.

Slash commands: `/hellor_request`, `/hellor_t17idadmin`.

How to use: Members read the posted board. Staff use `/hellor_request` to force a refresh and `/hellor_t17idadmin` to inspect stored T17 mappings if people are missing.

Rules and notes: The leaderboard depends on valid T17 lookups. If someone is not appearing, the usual cause is missing or bad mapping data rather than the Discord side of the cog.