package tema2.t01;

import java.util.Scanner;

public class ej22 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        int numero = 0, anterior = 0;
        boolean repetido = false;
        boolean primerNumero = true;

        System.out.println("Introduce numeros (un número negativo para terminar):");

        while (true) {
            numero = sc.nextInt();

            if (numero < 0) {
                break;
            }

            if (!primerNumero && numero == anterior) {
                repetido = true;
            }

            anterior = numero;
            primerNumero = false;
        }

        System.out.println(repetido ? "Si hubo repeticion" : "No hubo repeticion");
    }
}
