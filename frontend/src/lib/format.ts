/** How numbers are said in the console. One place, so two screens cannot disagree. */

/** Slicer seconds as "1 h 49 min" or "27 min", the way a print queue reads. */
export function formatMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** "4 Sep, 18:22:07". Absolute, because a run gets compared against a log. */
export function formatWhen(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  return at.toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

/** dapi prices are USD and always shown to the cent. */
export const money = (amount: number): string => `$${amount.toFixed(2)}`;
