(function () {
  if (typeof mermaid === 'undefined') {
    return;
  }

  var style = getComputedStyle(document.documentElement);
  var bg = style.getPropertyValue('--bg').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var soft = style.getPropertyValue('--soft').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var accent = style.getPropertyValue('--accent').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bodyFont = style.getPropertyValue('--font-body').trim();

  mermaid.initialize({
    startOnLoad: true,
    theme: 'neutral',
    securityLevel: 'strict',
    flowchart: { htmlLabels: true, useMaxWidth: true, curve: 'basis' },
    sequence: { useMaxWidth: true, mirrorActors: false },
    themeVariables: {
      fontFamily: bodyFont,
      fontSize: '14px',
      primaryColor: soft,
      primaryTextColor: ink,
      primaryBorderColor: accent,
      lineColor: muted,
      secondaryColor: bg2,
      tertiaryColor: bg,
      clusterBkg: bg2,
      clusterBorder: rule,
      edgeLabelBackground: bg2,
      actorBkg: soft,
      actorBorder: accent,
      actorTextColor: ink,
      actorLineColor: rule,
      signalColor: muted,
      signalTextColor: ink,
      labelBoxBkgColor: bg2,
      labelBoxBorderColor: rule,
      labelTextColor: ink,
      loopTextColor: ink,
      noteBkgColor: bg,
      noteBorderColor: rule,
      noteTextColor: ink
    }
  });
})();
