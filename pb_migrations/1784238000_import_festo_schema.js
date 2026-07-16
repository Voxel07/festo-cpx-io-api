// Keep the committed PocketBase collection schema reproducible in new and
// existing deployments. importCollections(..., false) extends the database and
// deliberately preserves unrelated collections such as the normal users auth
// collection.
migrate((app) => {
  const schema = $os.readFile("/pb/pocketbase_schema.json")
  app.importCollectionsByMarshaledJSON(schema, false)
}, (app) => {
  // Collection removal would also delete test history, so rollback is
  // intentionally non-destructive.
})
