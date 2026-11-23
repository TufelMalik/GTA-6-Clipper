#!/usr/bin/env bash
# Usage: ./render.sh -i in.mp4 -s 12.5 -e 41 -l A|B -o out.mp4 [-a captions.ass] [-t "HOOK TEXT"]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.env"

IN="" S="" E="" L="A" OUT="" ASS="" HOOK="" FC_IN="" FC_CROP="" FC_FIT=""
while getopts "i:s:e:l:o:a:t:f:c:m:p:" o; do
  case $o in i) IN=$OPTARG;; s) S=$OPTARG;; e) E=$OPTARG;; l) L=$OPTARG;;
             o) OUT=$OPTARG;; a) ASS=$OPTARG;; t) HOOK=$OPTARG;; f) FC_IN=$OPTARG;;
             c) FC_CROP=$OPTARG;; m) FC_FIT=$OPTARG;; p) B_TOP_PCT=$OPTARG;; *) exit 1;; esac
done
[[ -z $IN || -z $S || -z $E || -z $OUT ]] && { sed -n '2p' "$0"; exit 1; }

IN="$(realpath "$IN")"; OUT="$(realpath -m "$OUT")"
[[ -n $ASS ]] && ASS="$(realpath "$ASS")"
[[ -n $FC_IN ]] && FC_IN="$(realpath "$FC_IN")"
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
cd "$W"                                   # relative paths in filters = no Windows/colon escaping trouble
cp -r "$HERE/fonts" fonts 2>/dev/null || mkdir fonts
[[ -n $ASS ]] && cp "$ASS" cap.ass

even() { echo $(( $1 / 2 * 2 )); }
# centered/offset crop of the source with aspect OUT_W:TARGET_H -> prints "cw ch cx"
crop_for() {
  local th=$1 mx=$2 cw ch cx
  cw=$(( SRC_H * OUT_W / th )); ch=$SRC_H
  if (( cw > SRC_W )); then cw=$SRC_W; ch=$(( SRC_W * th / OUT_W )); fi
  cw=$(even $cw); ch=$(even $ch)
  if [[ $mx == auto ]]; then cx=$(( (SRC_W - cw) / 2 )); else cx=$mx; fi
  echo "$cw $ch $cx"
}

FACE="crop=${FACE_W}:${FACE_H}:${FACE_X}:${FACE_Y}"
FC_CROP_FILTER=""
if [[ -n $FC_CROP ]]; then
  if [[ $FC_CROP == crop=* ]]; then
    FC_CROP_FILTER="$FC_CROP"
  else
    FC_CROP_FILTER="crop=${FC_CROP}"
  fi
fi

FC_ARGS=()
if [[ -n $FC_IN ]]; then
  FC_ARGS=(-ss "$S" -to "$E" -i "$FC_IN")
fi

if [[ $L == A ]]; then
  read cw ch cx <<< "$(crop_for "$A_PANEL_H" "${A_MAIN_X:-auto}")"
  SW=$(( OUT_W / 4 )); SH=$(( OUT_H / 4 ))
  if [[ -n $FC_IN ]]; then
    if [[ -n $FC_CROP_FILTER ]]; then
      FG="[0:v]split=2[a][b];
[a]scale=${OUT_W}:${OUT_H}:force_original_aspect_ratio=increase,crop=${OUT_W}:${OUT_H},scale=${SW}:${SH},boxblur=${A_BLUR}:2,scale=${OUT_W}:${OUT_H},eq=brightness=-0.06[bg];
[b]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${A_PANEL_H}[main];
[1:v]${FC_CROP_FILTER},scale=${A_FACE_W}:-2[face];
[bg][main]overlay=0:(H-h)/2[t1];
[t1][face]overlay=W-w-${A_FACE_RIGHT}:H-h-${A_FACE_BOTTOM}[v0]"
    else
      FG="[0:v]split=2[a][b];
[a]scale=${OUT_W}:${OUT_H}:force_original_aspect_ratio=increase,crop=${OUT_W}:${OUT_H},scale=${SW}:${SH},boxblur=${A_BLUR}:2,scale=${OUT_W}:${OUT_H},eq=brightness=-0.06[bg];
[b]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${A_PANEL_H}[main];
[1:v]scale=${A_FACE_W}:-2[face];
[bg][main]overlay=0:(H-h)/2[t1];
[t1][face]overlay=W-w-${A_FACE_RIGHT}:H-h-${A_FACE_BOTTOM}[v0]"
    fi
  else
    CROP_TO_USE="${FC_CROP_FILTER:-$FACE}"
    FG="[0:v]split=3[a][b][c];
[a]scale=${OUT_W}:${OUT_H}:force_original_aspect_ratio=increase,crop=${OUT_W}:${OUT_H},scale=${SW}:${SH},boxblur=${A_BLUR}:2,scale=${OUT_W}:${OUT_H},eq=brightness=-0.06[bg];
[b]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${A_PANEL_H}[main];
[c]${CROP_TO_USE},scale=${A_FACE_W}:-2[face];
[bg][main]overlay=0:(H-h)/2[t1];
[t1][face]overlay=W-w-${A_FACE_RIGHT}:H-h-${A_FACE_BOTTOM}[v0]"
  fi
else
  TOP=$(even $(( OUT_H * B_TOP_PCT / 100 ))); BOT=$(( OUT_H - TOP ))
  read cw ch cx <<< "$(crop_for "$BOT" "${B_MAIN_X:-auto}")"
  EFFECTIVE_FIT="${FC_FIT:-$B_FACE_FIT}"
  if [[ $EFFECTIVE_FIT == stretch || $EFFECTIVE_FIT == fill ]]; then
    TOPF="scale=${OUT_W}:${TOP}"
  elif [[ $EFFECTIVE_FIT == fit || $EFFECTIVE_FIT == contain ]]; then
    TOPF="scale=${OUT_W}:${TOP}:force_original_aspect_ratio=decrease,pad=${OUT_W}:${TOP}:(ow-iw)/2:(oh-ih)/2:black"
  else
    TOPF="scale=${OUT_W}:${TOP}:force_original_aspect_ratio=increase,crop=${OUT_W}:${TOP}"
  fi
  if [[ -n $FC_IN ]]; then
    if [[ -n $FC_CROP_FILTER ]]; then
      FG="[1:v]${FC_CROP_FILTER},${TOPF}[top];
[0:v]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${BOT}[bot];
[top][bot]vstack,drawbox=x=0:y=$(( TOP - B_DIV_PX / 2 )):w=iw:h=${B_DIV_PX}:color=${B_DIV_COLOR}:t=fill[v0]"
    else
      FG="[1:v]${TOPF}[top];
[0:v]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${BOT}[bot];
[top][bot]vstack,drawbox=x=0:y=$(( TOP - B_DIV_PX / 2 )):w=iw:h=${B_DIV_PX}:color=${B_DIV_COLOR}:t=fill[v0]"
    fi
  else
    CROP_TO_USE="${FC_CROP_FILTER:-$FACE}"
    FG="[0:v]split=2[f][g];
[f]${CROP_TO_USE},${TOPF}[top];
[g]crop=${cw}:${ch}:${cx}:0,scale=${OUT_W}:${BOT}[bot];
[top][bot]vstack,drawbox=x=0:y=$(( TOP - B_DIV_PX / 2 )):w=iw:h=${B_DIV_PX}:color=${B_DIV_COLOR}:t=fill[v0]"
  fi
  MEME_Y=$(( TOP + BOT * MEME_Y_PCT / 100 ))
fi

LAST=v0
if [[ -n $HOOK ]]; then
  [[ $L == A ]] && MEME_Y=$(( OUT_H * 12 / 100 ))
  printf '%s' "$HOOK" | fold -s -w "$MEME_WRAP" > hook.txt
  FONT_FILE="${FONT_FILE:-fonts/Anton-Regular.ttf}"
  FF="font='Anton'"; [[ -f $HERE/$FONT_FILE ]] && FF="fontfile=${FONT_FILE}"
  EN=""
  if (( $(echo "$MEME_SECONDS > 0" | bc -l 2>/dev/null || python3 -c "import sys; print(1 if float(sys.argv[1])>0 else 0)" "$MEME_SECONDS" 2>/dev/null || echo 0) )); then
    EN=":enable='lt(t,${MEME_SECONDS})'"
  fi
  FG="${FG};
[${LAST}]drawtext=${FF}:textfile=hook.txt:fontsize=${MEME_SIZE}:fontcolor=white:borderw=${MEME_BORDER}:bordercolor=black:line_spacing=6:x=(w-text_w)/2:y=${MEME_Y}${EN}[v1]"
  LAST=v1
fi
if [[ -n $ASS ]]; then
  FG="${FG};
[${LAST}]ass=cap.ass:fontsdir=fonts[v2]"
  LAST=v2
fi

case "$PRESET" in
  1) PRESET="slow" ;;
  2) PRESET="medium" ;;
  3) PRESET="fast" ;;
  4) PRESET="veryfast" ;;
  5) PRESET="ultrafast" ;;
esac

if [[ $ENCODER == h264_qsv ]]; then VENC=(-c:v h264_qsv -global_quality 23 -look_ahead 0)
else VENC=(-c:v libx264 -preset "$PRESET" -crf "$CRF"); fi

ffmpeg -hide_banner -y -ss "$S" -to "$E" -i "$IN" "${FC_ARGS[@]}" \
  -filter_complex "$FG" -map "[$LAST]" -map 0:a? \
  "${VENC[@]}" -pix_fmt yuv420p -c:a aac -b:a 160k -movflags +faststart "$OUT"
echo "OK -> $OUT"
