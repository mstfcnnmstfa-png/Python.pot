const http = require('http');

const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('OK');
});

const PORT = 8080;
server.listen(PORT, '0.0.0.0', () => {
    console.log(`✅ HTTP server running on port ${PORT}`);
});
