# Historical Portrait Studio

## Enable generation

Open **Settings → AI Portrait Settings**. Check **Enable AI portrait generation**, choose a provider, enter its setup, and save. Use **Check saved connection** after saving. The check does not generate an image. Turning AI off blocks new generation but keeps original photos and completed gallery images.

- **OpenAI:** enter your own API key and a GPT Image edit model your account supports. API credits and image-model access are required separately from a ChatGPT subscription. The selected image and instructions go to OpenAI only when you request generation. Online accounts must supply their own key; enabling this setting does not spend the deployment owner’s credits.
- **Local AI (desktop only):** a running ComfyUI service needs the Decades `/decades/generate` reference-image bridge. The ordinary ComfyUI installation does not implement that endpoint on its own. Use a loopback address such as `http://127.0.0.1:8188`.

New keys are encrypted in a private per-user settings table. They are not stored in a save record, browser session, general UI preferences, or shared save export. Desktop encryption uses a device-local `portrait-key.secret` next to the desktop provider configuration. Keep that file private; after moving a database to another device, enter the API key again. Hosted encryption relies on the configured server session secret; changing it requires re-entering saved keys.

## Generate a portrait

1. Open a Sim’s profile and import a Tray portrait or upload a photo under **Life stages**.
2. Choose **Generate historical portrait** beneath that photo, or open **History → Portrait Studio** and select the Sim.
3. Review the reference, life stage, historical year, region and portrait style. Add social class, occupation, occult features or other art direction if useful.
4. Press **Generate portrait** once. The tracker remains usable while generation runs.

The year comes first from a recorded completed aging check, then the selected aging rules, then the calendar-scaled standard life-stage schedule. Twelve-day years scale the four-day stage offsets by three. Year-only birth records and fallback estimates are labeled as estimates. You can correct the suggested year without editing the Sim’s dates. For immortal/custom-age Sims, review the suggestion. A year entered manually is explicitly labeled.

Generation uses the reference for identity and asks for age-, year- and region-appropriate dress, hair, materials and setting. It is an **AI interpretation**, not proof of historical accuracy or a confirmed game observation. Review the result.

## Gallery and safeguards

- Every successful generation becomes a separate gallery record. Original portraits are never replaced.
- Browse the save-wide gallery, filter by Sim, or see recent results on the Sim’s profile. Full-sized viewing, download, and recoverable archive controls are provided.
- Each entry retains the stage, year, date calculation/source, source-photo identity and hash, provider/model, and instructions used.
- Completed gallery records and image blobs use the existing save-sync formats. Provider credentials never join them.
- Duplicate submissions with the same request identifier and repeated worker dispatches cannot generate twice. Provider requests are not silently retried. A fresh generation is a separate request and may cost credits.
- At most four recent jobs wait/run per save; each server process runs at most two image calls concurrently. No database transaction remains open during an image call.
- If the tracker closes or a provider times out, a pending job is not automatically replayed. Check provider usage before creating a new request.
- Tray imports now honor the game’s transparency mask and retain their original detail. Re-scan old imported portraits after installing this update to remove the old blurred edge artifacts. Manual portraits stay protected.

API implementation reference: [OpenAI image generation and reference-image editing](https://developers.openai.com/api/docs/guides/image-generation).
