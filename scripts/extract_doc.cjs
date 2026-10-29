const WordExtractor = require('word-extractor');
new WordExtractor().extract(process.argv[2]).then(doc => {
  process.stdout.write(JSON.stringify({body: doc.getBody()}));
}).catch(() => { process.stderr.write('DOC extraction failed'); process.exitCode = 1; });
