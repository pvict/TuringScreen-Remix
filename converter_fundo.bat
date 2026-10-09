@echo off
echo ========================================================
echo   Conversor Automatico do Video de Fundo para Turing Screen
echo ========================================================
set /p input_file="Digite o nome do video original de fundo (ex: fundo.mp4): "

if not exist "%input_file%" (
    echo [ERRO] O ficheiro "%input_file%" nao foi encontrado!
    pause
    exit
)

echo Processando o video de fundo com os ajustes otimizados...
ffmpeg -i "%input_file%" -vf "scale=480:480:force_original_aspect_ratio=increase,crop=480:480,eq=saturation=1.3:contrast=1.1" -r 30 -an -c:v libx264 -crf 15 -preset medium -profile:v baseline -bf 0 -pix_fmt yuv420p -y video_fundo.mp4

echo A limpar o cache antigo de fundo (.h264)...
if exist video_fundo.mp4.h264 del video_fundo.mp4.h264

echo ========================================================
echo   Conversao do fundo concluida com sucesso!
echo ========================================================
pause