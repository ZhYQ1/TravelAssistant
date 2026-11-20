(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var bodyFont = style.getPropertyValue('--font-body').trim();
  var container = document.getElementById('chart-route-latency');

  if (!container || typeof echarts === 'undefined') {
    return;
  }

  var chart = echarts.init(container, null, { renderer: 'svg' });
  chart.setOption({
    animation: false,
    backgroundColor: bg2,
    aria: {
      enabled: true,
      description: '固定六十条回归集两轮对比中，优化前平均路由延迟为 1189.8 毫秒，优化后为 547.2 毫秒，下降百分之五十四。'
    },
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      axisPointer: { type: 'shadow' },
      formatter: function (params) {
        var item = params[0];
        return item.name + '<br/>平均路由延迟：<strong>' + Number(item.value).toFixed(1) + ' ms</strong>';
      }
    },
    grid: { left: 64, right: 30, top: 34, bottom: 58 },
    xAxis: {
      type: 'category',
      data: ['优化前', '优化后'],
      axisTick: { show: false },
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: ink, fontSize: 13, fontFamily: bodyFont }
    },
    yAxis: {
      type: 'value',
      name: '平均延迟（ms）',
      max: 1300,
      nameTextStyle: { color: muted, fontFamily: bodyFont, padding: [0, 0, 8, 0] },
      axisLabel: { color: muted, fontFamily: bodyFont },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } }
    },
    series: [{
      name: '平均路由延迟',
      type: 'bar',
      barWidth: '42%',
      data: [
        { value: 1189.8, itemStyle: { color: accent2 } },
        { value: 547.2, itemStyle: { color: accent } }
      ],
      label: {
        show: true,
        position: 'top',
        color: ink,
        fontSize: 14,
        fontWeight: 700,
        fontFamily: bodyFont,
        formatter: function (params) { return Number(params.value).toFixed(1) + ' ms'; }
      },
      itemStyle: { borderRadius: [5, 5, 0, 0] }
    }]
  });

  window.addEventListener('resize', function () { chart.resize(); });
})();
