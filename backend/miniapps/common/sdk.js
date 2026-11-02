// Stories 小程序公共 SDK：token 注入 + API 封装（所有小程序共用）
(function () {
  var qs = new URLSearchParams(location.search);
  var TOKEN = qs.get('token') || '';
  function api(path, opts) {
    opts = opts || {};
    var url = path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN);
    return fetch(url, {
      method: opts.method || 'GET',
      headers: { 'Content-Type': 'application/json', 'X-App-Token': TOKEN },
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }
  window.MINI = {
    token: TOKEN,
    api: api,
    kvGet: function (app, key) {
      return api('/api/mini/kv?app=' + encodeURIComponent(app) + '&key=' + encodeURIComponent(key));
    },
    kvSet: function (app, key, value) {
      return api('/api/mini/kv', { method: 'POST', body: { app: app, key: key, value: value } });
    },
    event: function (app, content, cid) {
      return api('/api/mini/events', { method: 'POST', body: { app: app, content: content, cid: cid || 0 } });
    },
    weather: function (city) {
      return api('/api/mini/weather?city=' + encodeURIComponent(city));
    },
    esc: function (s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    }
  };
})();
