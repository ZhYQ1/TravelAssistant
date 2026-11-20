(function () {
  function scrollToHash(hash, updateHistory) {
    if (!hash || hash.charAt(0) !== '#') {
      return false;
    }

    var target = document.getElementById(decodeURIComponent(hash.slice(1)));
    if (!target) {
      return false;
    }

    if (updateHistory) {
      if (window.history && window.history.pushState) {
        window.history.pushState(null, '', hash);
      } else {
        window.location.hash = hash;
      }
    }

    target.scrollIntoView({ behavior: 'auto', block: 'start' });
    return true;
  }

  var anchors = document.querySelectorAll('a[href^="#"]');
  Array.prototype.forEach.call(anchors, function (anchor) {
    anchor.addEventListener('click', function (event) {
      var hash = anchor.getAttribute('href');
      if (scrollToHash(hash, true)) {
        event.preventDefault();
      }
    });
  });

  window.addEventListener('load', function () {
    if (window.location.hash) {
      window.requestAnimationFrame(function () {
        scrollToHash(window.location.hash, false);
      });
    }
  });
})();
