/**
 * The historical serverless Express handler is permanently unavailable.
 * Returning a fixed 410 avoids importing any of the retired mock routes.
 */
export default function retiredLegacyApi(
  _request: unknown,
  response: { status: (code: number) => { json: (body: object) => unknown } },
) {
  return response.status(410).json({
    error: 'The legacy API has been retired. Deploy the supported FastAPI application.',
  });
}
