/**
 * Member photos, as the API serves them: bioguide.congress.gov's portrait
 * for a member's bioguide id (backend `_bioguide_photo`, in
 * app/api/politicians.py and app/services/bill_service.py). One copy of the
 * id pattern and the URL shape for the frontend, shared by the share-image
 * capture (which recognises these URLs) and the same-origin photo route
 * (which fetches them), so the two can't drift apart.
 */
export const BIOGUIDE_ID = /^[A-Z]\d{6}$/;

/** The official portrait URL for a bioguide id. */
export function bioguidePhotoUrl(id: string): string {
  return `https://bioguide.congress.gov/bioguide/photo/${id[0]}/${id}.jpg`;
}

/** The bioguide id in a portrait URL of that shape, or null. */
export function bioguideIdFromPhotoUrl(url: string): string | null {
  const m = /^https:\/\/bioguide\.congress\.gov\/bioguide\/photo\/[A-Z]\/([A-Z]\d{6})\.jpg$/.exec(
    url
  );
  return m && bioguidePhotoUrl(m[1]) === url ? m[1] : null;
}
