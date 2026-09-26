# ScrcpyGate UI layout rules

These rules cover all ten pages in `static/pages/`. They apply the hierarchy and interaction principles in Apple's [Layout](https://developer.apple.com/design/human-interface-guidelines/layout), [Labels](https://developer.apple.com/design/human-interface-guidelines/labels), [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars), and [Segmented controls](https://developer.apple.com/design/human-interface-guidelines/segmented-controls) guidance while retaining ScrcpyGate's existing palette, themes, icons, and native HTML/CSS/JS stack. They are not a pixel-for-pixel iOS imitation.

## Hierarchy

1. The global bar contains navigation, account, and theme controls. The page heading identifies the current task; page-level refresh or creation actions may sit beside it.
2. A control that replaces the main page content sits immediately below the heading and uses the shared segmented treatment. A control that affects one table, chart, or dialog stays inside that surface, close to the content it changes.
3. Group content by task. A heading, filters, results, and pagination for one task share one container. Use spacing, type weight, and surface color before adding inner divider lines.
4. Put commands near their target. Save and reset actions follow the form. Destructive actions keep clear text, color, and confirmation behavior.
5. Keep labels short and adjacent to their fields or information buttons. Errors, risks, results, and live status remain visible without hover. Useful values such as IP addresses, timestamps, and log text remain selectable.

## Page map

| Page | Primary task | Page-level choice | Local choice |
| --- | --- | --- | --- |
| Login | Authenticate | None | Language, theme, and verification stay with the form |
| Mirror | Prioritize the video viewport | Single/grid mode sits beside the workspace title | Sidebar navigation shows names only; quality, controls, recording, and account stay in their panels |
| Dashboard | Scan devices and attention items | None | Device filters and alert views stay in their panels |
| Devices | Browse devices and details | None | Search and filters stay with the list |
| Users | Browse and manage users | None | Filters stay inside the list; account policy is a separate disclosure |
| Quality | Configure video and sessions | Quality/transport/session | Preset actions stay with presets |
| Mirror administration | Configure by role | Admin/user | Button-level choices stay with their settings |
| ALAS | Manage ownership and configuration | Users/configurations/visibility | Status is shared; list filters stay in the list |
| Security | Manage access, bans, geo, and login | Four security sections | Filters and downloads stay in their section |
| Logs | Search runtime and audit events | None | Runtime/audit stays in the log panel; structured/raw stays with runtime logs |

## Sizing and accessibility

- Admin controls are compact at 36px, with 16px content spacing. Primary touch controls are at least 44px high, with 12px content spacing on narrow screens. `admin-workspace.css`, `interface-controls.css`, and existing tokens own the values.
- The admin rail shows one clear label per destination. Dashboard and log summaries use a compact information band; device and ALAS summaries use smaller independent cards that wrap into two columns on narrow screens.
- In the mirror workbench, keep the two device-view choices together beside the workspace title. The sidebar owns device selection; the status line leads with the current device and control ownership. The bottom toolbar keeps session and control actions visible, moving secondary actions into More when space is limited. Selected and error states remain distinct.
- Use ScrcpyGate's own radius scale on all ten pages: 8px for fields, command buttons, and icon buttons; 12px outside and 9px inside segmented choices; 16px for peer cards and centered dialogs. These pixel values are project decisions, not Apple HIG specifications. Status badges, avatars, switch tracks, and genuinely circular indicators retain their semantic shapes. Keep each compound field's border on its outer wrapper only.
- Every field has a visible label or a clear accessible name; a placeholder is not a label. Both locales must wrap naturally.
- Reflow by available width on phones and tablets. Segmented controls may scroll without a visible scrollbar when space is scarce, while all choices remain keyboard focusable. Preserve video space in the mirror workbench and necessary controls in fullscreen.
- Use existing semantic colors in both themes, with text or icons alongside color. Keep keyboard focus visible and honor reduced-motion preferences.

When editing a page, check heading, page-level choice, task container, local actions, save area, and dialogs against this map, then inspect desktop, tablet, phone, long text, both themes, keyboard, and touch. Layout work must not change API, permission, WebSocket, or business-state semantics.
